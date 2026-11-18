"""Wartemusik fuer die Rueckfrage vor dem Verbinden.

Asterisk spielt dem Anrufer waehrend des Klingelns und der Rueckfrage Musik (Dial-Option m, MoH-Klasse default):
eine eingebaute Melodie (wird bei Bedarf erzeugt, frei von Rechten Dritter).
Die Stuecke (eingebaute Melodie und hochgeladene Dateien) verwaltet die Weboberflaeche und liegen in data/sounds/moh/lib/. Fuer jedes Stueck
erzeugt die Bridge MoH-Klassen (Verzeichnisse mit Verweisen in data/sounds/moh/c/ und die Datei data/asterisk/moh.conf, die Asterisk einliest):
  m_<id>   Musik ab diesem Stueck, danach die uebrigen eingeschalteten der Reihe nach (kein Stueck zweimal hintereinander)
  r_<id>   dasselbe, aber zuerst n Freizeichen-Toene (Musik beim Klingeln)
  default  alle eingeschalteten (Rueckfall)
Welche Klasse ein Anruf bekommt, entscheidet pick_class(): ein fest gewaehltes Stueck oder zufaellig eines der eingeschalteten, nie dasselbe wie zuvor."""
import asyncio
import base64
import io
import json
import logging
import math
import os
import re
import secrets
import shutil
import struct
import wave

from . import ami, db

log = logging.getLogger("hold")
SOUNDS = os.path.join(os.path.dirname(os.environ.get("DB_PATH", "/data/lieschen.db")), "sounds")
MOH = os.path.join(SOUNDS, "moh")
LIB_DIR, TONE_DIR, CLASS_DIR = os.path.join(MOH, "lib"), os.path.join(MOH, "tones"), os.path.join(MOH, "c")
PLAY_DIR = os.path.join(MOH, "play")          # Wiedergabe-Kopien der Bibliothek mit eingestellter Lautstaerke
CONF = os.path.join(os.path.dirname(SOUNDS), "asterisk", "moh.conf")      # wird von Asterisk per #include gelesen (/etc/asterisk/dyn/moh.conf)
MAX_TRACKS, MAX_BYTES = 30, 25 * 1024 * 1024
LAST = {}                      # Verwendung ("ring"/"screen") -> zuletzt gewaehltes Stueck (kein Stueck zweimal hintereinander)


def _wav_files(path):
    try:
        return [f for f in sorted(os.listdir(path)) if f.lower().endswith(".wav")]
    except OSError:
        return []


def _write_wav(path, pcm, rate=8000):
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


_N = {"C3": 130.81, "F3": 174.61, "G3": 196.0, "A3": 220.0, "B3": 246.94, "C4": 261.63, "D4": 293.66, "E4": 329.63,
      "G4": 392.0, "A4": 440.0, "B4": 493.88, "C5": 523.25, "D5": 587.33}
_SR = 8000


def _mix(out, start, samples, gain):
    n0 = int(start * _SR)
    for i, v in enumerate(samples):
        j = n0 + i
        if j >= len(out):
            break
        out[j] += v * gain


def _tone(f, dur, attack, decay, harm, vib=0.0):
    """Spieluhr-Ton: weicher Anschlag, exponentielles Ausklingen, leichte Obertoene."""
    res = []
    for i in range(int(dur * _SR)):
        t = i / _SR
        ph = 2 * math.pi * f * t + (vib * math.sin(2 * math.pi * 5 * t) if vib else 0)
        res.append(min(1.0, t / attack) * math.exp(-decay * t) * sum(h * math.sin(ph * (k + 1)) for k, h in enumerate(harm)))
    return res


def _pad(freqs, dur, attack, release):
    """Weiche Flaeche: langsam ein- und ausblendende Toene mit leichtem Wabern."""
    n = int(dur * _SR)
    res = [0.0] * n
    for f in freqs:
        for i in range(n):
            t = i / _SR
            env = min(1.0, t / attack) * min(1.0, max(0.0, (dur - t) / release))
            res[i] += env * (1 + 0.06 * math.sin(2 * math.pi * 0.18 * t + f)) * (math.sin(2 * math.pi * f * t) + 0.12 * math.sin(4 * math.pi * f * t))
    return res


def _echo(out, delay, fb, taps):
    d, res = int(delay * _SR), list(out)
    for k in range(1, taps + 1):
        for i in range(len(out) - d * k):
            res[i + d * k] += out[i] * fb ** k
    return res


def _melody():
    """Wiegenlied im 3/4-Takt: Spieluhr-Begleitung (Akkorde C-F-Am-G) mit einer einfachen eigenen Melodie, 8 kHz, ca. 90 s."""
    beat = 60 / 66
    bar = 3 * beat
    bars = [("C3", ["E4", "G4"]), ("C3", ["E4", "G4"]), ("F3", ["A3", "C4"]), ("C3", ["E4", "G4"]),
            ("A3", ["C4", "E4"]), ("F3", ["A3", "C4"]), ("G3", ["B3", "D4"]), ("C3", ["E4", "G4"])]
    mel = [(0, 0, "G4", 1), (0, 1, "A4", 1), (0, 2, "C5", 1), (1, 0, "A4", 2), (1, 2, "G4", 1),
           (2, 0, "E4", 1), (2, 1, "G4", 1), (2, 2, "A4", 1), (3, 0, "G4", 3),
           (4, 0, "A4", 1), (4, 1, "C5", 1), (4, 2, "D5", 1), (5, 0, "C5", 2), (5, 2, "A4", 1),
           (6, 0, "G4", 1), (6, 1, "A4", 1), (6, 2, "B4", 1), (7, 0, "C5", 3)]
    passes = 4
    out = [0.0] * int(_SR * (bar * len(bars) * passes + 4))
    t0 = 0.0
    for p in range(passes):
        for i, (bass, notes) in enumerate(bars):
            tb = t0 + i * bar
            _mix(out, tb, _pad([_N[bass], _N[bass] * 2], bar + 0.5, 0.9, 1.0), 0.16)
            _mix(out, tb, _tone(_N[bass] * 2, 2.4, 0.015, 1.6, (1.0, 0.18, 0.05)), 0.26)
            for b in (1, 2):
                _mix(out, tb + b * beat, _tone(_N[notes[b - 1]], 1.8, 0.012, 2.6, (1.0, 0.2, 0.06)), 0.22)
        if p % 4 != 3:                                  # Melodie in drei von vier Durchlaeufen, dann nur Begleitung
            for bi, bt, nt, ln in mel:
                _mix(out, t0 + bi * bar + bt * beat, _tone(_N[nt], 2.6 + ln * 0.4, 0.012, 1.9, (1.0, 0.2, 0.06), 0.3), 0.36)
        t0 += bar * len(bars)
    out = _echo(out, 0.48, 0.30, 3)
    fade = int(1.5 * _SR)
    for i in range(fade):                               # sanft ein- und ausblenden
        out[i] *= i / fade
        out[-1 - i] *= i / fade
    rms = math.sqrt(sum(x * x for x in out) / len(out)) or 1e-9      # auf den Pegel der uebrigen Stuecke bringen (TARGET_RMS)
    k = min(TARGET_RMS / rms, 30000 / max(1e-9, max(abs(x) for x in out)))
    return b"".join(struct.pack("<h", int(x * k)) for x in out)


def _tracks():
    try:
        return json.loads(db.get_meta("hold_tracks", "") or "[]")
    except ValueError:
        return []


def _save(tracks):
    db.set_meta("hold_tracks", json.dumps(tracks, ensure_ascii=False))


def _path(t):
    return os.path.join(LIB_DIR, t["id"] + ".wav")


def _path16(t):
    return os.path.join(LIB_DIR, t["id"] + ".wav16")        # Breitband-Fassung (16 kHz) fuer G.722; Asterisk waehlt je Kanal die passende Datei


def _secs(path):
    try:
        with wave.open(path) as w:
            return round(w.getnframes() / w.getframerate(), 1)
    except (OSError, wave.Error):
        return 0.0


def ensure_default():
    """Verzeichnisse anlegen, alte Ablage (default/off) uebernehmen und die eingebaute Melodie als Stueck 'melodie' bereitstellen (Version 3 = Wiegenlied)."""
    for d in (LIB_DIR, TONE_DIR, CLASS_DIR):
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o755)
    for old in ("default", "off", "ring"):                   # frueheres Layout: Dateien in die Bibliothek verschieben, Reste entfernen
        od = os.path.join(MOH, old)
        if os.path.isdir(od):
            for f in os.listdir(od):
                if f.endswith(".wav") and not f.startswith("_") and not os.path.exists(os.path.join(LIB_DIR, f)) and old != "ring":
                    os.replace(os.path.join(od, f), os.path.join(LIB_DIR, f))
                else:
                    try:
                        os.remove(os.path.join(od, f))
                    except OSError:
                        pass
            try:
                os.rmdir(od)
            except OSError:
                pass
    tracks = _tracks()
    own = next((t for t in tracks if t["id"] == "melodie"), None)
    if own is None:
        own = {"id": "melodie", "name": "Wiegenlied (eingebaut)", "active": True}
        tracks.insert(0, own)
    if not os.path.exists(_path(own)) or db.get_meta("hold_melody_v", "") != "5":
        _write_wav(_path(own), _melody())
        db.set_meta("hold_melody_v", "5")
        log.info("Wartemusik: eingebaute Melodie erzeugt")
    if not any(t["active"] for t in tracks):                 # es muss immer etwas spielen
        own["active"] = True
    tracks = [t for t in tracks if os.path.exists(_path(t))]
    _save(tracks)


def list_tracks():
    ensure_default()
    return [dict(t, secs=_secs(_path(t))) for t in _tracks()]


def _clean_name(name):
    n = re.sub(r"\.(mp3|wav|m4a|ogg|flac)$", "", str(name or ""), flags=re.I)
    n = re.sub(r"[\x00-\x1f<>]+", " ", n).strip()
    return n[:80] or "Musik"


TARGET_RMS = 3600.0       # Effektivpegel der Stuecke in der Bibliothek (vor dem Lautstaerke-Regler)


def _resample(x, sr, target):
    """x (float32, mono, sr Hz) auf target Hz: erst Tiefpass (Kaiser-FIR per FFT, Grenze knapp unter target/2), dann linear abtasten.
    Der Resampler von miniaudio filtert nicht und erzeugt bei 48 -> 8 kHz hoerbare Spiegelfrequenzen."""
    import numpy as np
    if sr == target:
        return x
    if sr > target:
        taps = 511
        fc = 0.46 * target / sr                              # normiert auf sr, 0.5 = Nyquist
        n = np.arange(taps) - taps // 2
        h = 2 * fc * np.sinc(2 * fc * n) * np.kaiser(taps, 8.0)
        h = (h / h.sum()).astype(np.float32)
        step, out = 1 << 18, []
        nfft = 1 << 19
        H = np.fft.rfft(h, nfft)
        for i in range(0, len(x), step):                      # Overlap-Save
            seg = x[max(0, i - taps): i + step + taps]
            y = np.fft.irfft(np.fft.rfft(seg, nfft) * H, nfft)
            lo = i - max(0, i - taps)
            out.append(y[lo + taps // 2: lo + taps // 2 + min(step, len(x) - i)])
        x = np.concatenate(out).astype(np.float32)
    pos = np.arange(int(len(x) * target / sr)) * (sr / target)
    return np.interp(pos, np.arange(len(x)), x).astype(np.float32)


def _mavg(a, n):
    import numpy as np
    n = max(1, int(n))
    c = np.concatenate(([0.0], np.cumsum(a, dtype=np.float64)))
    h = n // 2
    idx = np.arange(len(a))
    lo, hi = np.clip(idx - h, 0, len(a)), np.clip(idx - h + n, 0, len(a))
    return ((c[hi] - c[lo]) / np.maximum(hi - lo, 1)).astype(np.float32)


def _master(y, r):
    """Fuer das Telefonband aufbereiten: Bass unter ~200 Hz raus (kommt nicht an, bestimmt aber den Pegel), Mitten (1-3,4 kHz) anheben,
    leise Stellen heben und laute begrenzen (Kompressor mit 50-ms-Huellkurve), gleiche Lautheit fuer alle Stuecke; Ergebnis int16."""
    import numpy as np
    spec = np.fft.rfft(y)
    fr = np.fft.rfftfreq(len(y), 1 / r)
    g = np.clip((fr - 120) / 120, 0.0, 1.0)
    g *= 1 + 1.2 * np.exp(-(((fr - 2200) / 900) ** 2))
    g *= 1 + 0.5 * np.exp(-(((fr - 800) / 400) ** 2))
    y = np.fft.irfft(spec * g, len(y)).astype(np.float32)
    env = np.sqrt(_mavg(y * y, r * 0.05)) + 50
    y = y * _mavg(np.clip((TARGET_RMS / env) ** 0.6, 0.3, 4.0), r * 0.2)
    y *= TARGET_RMS / (float(np.sqrt(np.mean(y * y))) or 1.0)
    return (30000 * np.tanh(y / 30000)).astype("<i2")


def _decode(data, rates):
    """Datei einmal in voller Aufloesung lesen (mono) und auf jede Rate in rates bringen; Effektivpegel angleichen."""
    import miniaudio
    import numpy as np
    dec = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=48000)
    sr = 48000
    x = np.frombuffer(dec.samples.tobytes(), dtype="<i2").astype(np.float32)
    out = {}
    for r in rates:
        y = _master(_resample(x, sr, r), r)
        out[r] = y.tobytes()
    return out


def add_track(name, data):
    """MP3/WAV (u. a.) hochgeladen: nach 8 kHz (und 16 kHz fuer G.722) mono WAV wandeln, normalisieren und als aktives Stueck ablegen."""
    tracks = _tracks()
    if len(tracks) >= MAX_TRACKS:
        raise ValueError(f"Es sind höchstens {MAX_TRACKS} Stücke möglich")
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Die Datei ist leer oder größer als 25 MB")
    try:
        pcm = _decode(data, (8000, 16000))
    except Exception as e:
        raise ValueError("Die Datei konnte nicht gelesen werden (erlaubt: MP3, WAV, FLAC, OGG)") from e
    secs = len(pcm[8000]) / 2 / 8000
    if secs < 3 or secs > 900:
        raise ValueError("Das Stück muss zwischen 3 Sekunden und 15 Minuten lang sein")
    t = {"id": secrets.token_hex(4), "name": _clean_name(name), "active": True}
    _write_wav(_path(t), pcm[8000])
    _write_wav(_path16(t), pcm[16000], 16000)
    tracks.append(t)
    _save(tracks)
    return t


def set_active(tid, active):
    tracks = _tracks()
    t = next((t for t in tracks if t["id"] == tid), None)
    if t is None:
        raise KeyError(tid)
    if bool(active) == bool(t["active"]):
        return
    if not active and sum(1 for x in tracks if x["active"]) <= 1:
        raise ValueError("Mindestens ein Stück muss eingeschaltet bleiben")
    t["active"] = bool(active)
    _save(tracks)


def delete_track(tid):
    tracks = _tracks()
    t = next((t for t in tracks if t["id"] == tid), None)
    if t is None:
        raise KeyError(tid)
    if tid == "melodie":
        raise ValueError("Die eingebaute Melodie lässt sich nur ausschalten")
    if t["active"] and sum(1 for x in tracks if x["active"]) <= 1:
        raise ValueError("Mindestens ein Stück muss eingeschaltet bleiben")
    for f in (_path(t), _path16(t)):
        try:
            os.remove(f)
        except OSError:
            pass
    _save([x for x in tracks if x["id"] != tid])
    for k in [k for k, v in LAST.items() if v == tid]:
        LAST.pop(k, None)
    for ctx in ("ring_track", "screen_track"):               # fest gewaehltes, geloeschtes Stueck: zurueck auf "alle"
        if db.get_meta(ctx, "") == tid:
            db.set_meta(ctx, "")


def track_file(tid):
    t = next((t for t in _tracks() if t["id"] == tid), None)
    return _path(t) if t and os.path.exists(_path(t)) else None


def ring_after():
    """Wartemusik beim Klingeln: erst so viele Freizeichen-Toene, dann Musik (0 = sofort Musik)."""
    try:
        return max(0, min(int(db.get_meta("ring_music_after", "0") or 0), 8))
    except ValueError:
        return 0


def ring_delay():
    try:
        return max(0, min(int(db.get_meta("ring_delay", "0") or 0), 10))
    except ValueError:
        return 0


def _ringtones(n, skip=0):
    """Wie _ringtones_full, aber ohne die ersten 'skip' Sekunden (die hat der Dialplan schon als Freizeichen gespielt)."""
    return _ringtones_full(n)[skip * _SR * 2:]


def _ringtones_full(n):
    """n Rufzeichen wie das deutsche Freizeichen: 425 Hz, 1 s Ton, 4 s Pause (8 kHz, 16 Bit)."""
    tone = b"".join(struct.pack("<h", int(10000 * math.sin(2 * math.pi * 425 * i / _SR) * min(1.0, i / 80, (_SR - i) / 80))) for i in range(_SR))
    return (tone + bytes(4 * _SR * 2)) * (n - 1) + tone          # nach dem letzten Ton gleich die Musik (keine 4 s Pause mehr)


PLACEHOLDER = "_leer.wav"        # 0,5 s Stille: sorgt dafuer, dass die Klasse 'default' schon beim Start von Asterisk existiert (render.py legt sie an)


def _order(tracks, start):
    """IDs der eingeschalteten Stuecke als Ring, beginnend bei 'start' (auch wenn dieses ausgeschaltet, aber fest gewaehlt ist)."""
    act = [t["id"] for t in tracks if t["active"]]
    if start in act:
        i = act.index(start)
        return act[i:] + act[:i]
    return [start] + act


MOH_DB_PER_STEP = 0.45      # Lautstaerke-Skala in dB: 100 % = unveraendert, 10 % = -40,5 dB (frueher linear: 10 % = nur -20 dB)


def moh_gain(v):
    return 10 ** ((v - 100) * MOH_DB_PER_STEP / 20.0)


def moh_live_db():
    """Zusaetzliche Daempfung (dB, 0 bis 30) der Wartemusik beim Klingeln ueber VOLUME(TX) im Dialplan; bei Annahme setzt die Bridge sie zurueck."""
    try:
        return max(0, min(int(db.get_meta("moh_live_db", "0") or 0), 30))
    except ValueError:
        return 0


def moh_live_divisor():
    """VOLUME(TX) im Dialplan ist ein Teiler (-2 = Pegel durch 2, auch Bruchwerte wie -1.41), kein dB-Wert: Zusatzdaempfung in dB als negativer Teiler, 0 = aus."""
    d = moh_live_db()
    return "-%.2f" % (10 ** (d / 20.0)) if d else "0"


def moh_volume():
    """Lautstaerke der Wartemusik in Prozent (10 bis 100, dB-Skala); die Freizeichen-Toene bleiben unveraendert."""
    try:
        if db.get_meta("moh_vol_scale", "") != "2":         # Altwert war linear: auf gleiche Lautheit in der dB-Skala umrechnen
            old = max(10, min(int(db.get_meta("moh_volume", "60") or 60), 100))
            db.set_meta("moh_volume", str(round(100 + 20 * math.log10(old / 100.0) / MOH_DB_PER_STEP)))
            db.set_meta("moh_vol_scale", "2")
        return max(10, min(int(db.get_meta("moh_volume", "90") or 90), 100))
    except ValueError:
        return 90


def _make_play(tracks):
    """Kopien der Stuecke mit der eingestellten Lautstaerke in play/ schreiben (Klassen verweisen darauf)."""
    import numpy as np
    os.makedirs(PLAY_DIR, exist_ok=True)
    os.chmod(PLAY_DIR, 0o755)
    g = moh_gain(moh_volume())
    keep = set()
    for t in tracks:
        src, dst = _path(t), os.path.join(PLAY_DIR, t["id"] + ".wav")
        keep.add(t["id"] + ".wav")
        with wave.open(src) as w:
            pcm = w.readframes(w.getnframes())
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) * g
        _write_wav(dst, np.clip(x, -32768, 32767).astype("<i2").tobytes())
        if os.path.exists(_path16(t)):
            with wave.open(_path16(t)) as w:
                pcm = w.readframes(w.getnframes())
            x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) * g
            _write_wav(dst + "16", np.clip(x, -32768, 32767).astype("<i2").tobytes(), 16000)
            keep.add(t["id"] + ".wav16")
    for f in os.listdir(PLAY_DIR):
        if f not in keep:
            os.remove(os.path.join(PLAY_DIR, f))


def _link(cdir, name, target_rel):
    os.symlink(target_rel, os.path.join(cdir, name))


def rebuild_classes():
    """Verzeichnisse und moh.conf fuer alle Klassen neu schreiben (Stuecke, Schalter und Zahl der Freizeichen-Toene stehen in der Datenbank)."""
    ensure_default()
    tracks = _tracks()
    _make_play(tracks)
    n = ring_after()
    classes = {}
    d = ring_delay()
    tone_file = None
    if n > 0 and d < 5 * n - 4:               # Rest der Freizeichen-Toene nach der Verzoegerung (die ersten d Sekunden spielt der Dialplan selbst)
        tone_file = f"ring-{n}-{d}.wav"
        _write_wav(os.path.join(TONE_DIR, tone_file), _ringtones(n, d))
        for old in os.listdir(TONE_DIR):
            if old != tone_file:
                os.remove(os.path.join(TONE_DIR, old))
    elif os.path.isdir(TONE_DIR):
        for old in os.listdir(TONE_DIR):
            os.remove(os.path.join(TONE_DIR, old))
    for t in tracks:
        order = _order(tracks, t["id"])
        classes["m_" + t["id"]] = [("%02d-%s.wav" % (i, tid), f"../../play/{tid}.wav") for i, tid in enumerate(order, 1)]
        if n > 0:
            classes["r_" + t["id"]] = ([("00-ring.wav", f"../../tones/{tone_file}")] if tone_file else []) + classes["m_" + t["id"]]
    act = [t["id"] for t in tracks if t["active"]]
    classes["default"] = [("%02d-%s.wav" % (i, tid), f"../../play/{tid}.wav") for i, tid in enumerate(act, 1)] or [(PLACEHOLDER, "")]
    os.makedirs(CLASS_DIR, exist_ok=True)
    for old in os.listdir(CLASS_DIR):                         # veraltete Klassen entfernen
        if old not in classes:
            shutil.rmtree(os.path.join(CLASS_DIR, old), ignore_errors=True)
    for cname, entries in classes.items():
        cdir = os.path.join(CLASS_DIR, cname)
        shutil.rmtree(cdir, ignore_errors=True)
        os.makedirs(cdir, exist_ok=True)
        os.chmod(cdir, 0o755)
        for fname, rel in entries:
            if rel:
                _link(cdir, fname, rel)
                if os.path.exists(os.path.normpath(os.path.join(CLASS_DIR, cname, rel)) + "16"):
                    _link(cdir, fname + "16", rel + "16")
            else:
                _write_wav(os.path.join(cdir, fname), bytes(8000))
    lines = ["; Von der Bridge erzeugt (Wartemusik-Klassen). Nicht von Hand aendern."]
    for cname in classes:
        lines += [f"[{cname}]", "mode=files", f"directory=/sounds/moh/c/{cname}", "sort=alpha" if cname != "default" else "random=yes", ""]
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    tmp = CONF + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.chmod(tmp, 0o644)
    os.replace(tmp, CONF)


def pick_class(ctx):
    """MoH-Klasse fuer einen Anruf: ctx 'ring' (Musik beim Klingeln, ggf. nach n Freizeichen-Toenen) oder 'screen' (Verbinden/Rueckfrage).
    Fest gewaehltes Stueck (Einstellung <ctx>_track) oder zufaellig eines der eingeschalteten, nie dasselbe wie beim vorigen Anruf."""
    tracks = _tracks()
    ids = {t["id"] for t in tracks}
    sel = db.get_meta(ctx + "_track", "")
    if sel in ids:
        tid = sel
    else:
        act = [t["id"] for t in tracks if t["active"]]
        if not act:
            return "default"
        pool = [i for i in act if i != LAST.get(ctx)] or act
        tid = secrets.choice(pool)
    LAST[ctx] = tid
    if ctx == "ring" and ring_after() > 0:
        return "r_" + tid
    return "m_" + tid


async def reload_when_ready():
    """Nach dem Start: Asterisk die Klassen neu einlesen lassen (es startet evtl. vor der Bridge); wartet auf AMI."""
    for _ in range(12):
        try:
            await ami.command("moh reload")
            return
        except Exception:
            await asyncio.sleep(5)
    log.warning("Wartemusik: Asterisk war nicht erreichbar, die Klassen werden beim naechsten Neuladen gelesen")


async def reload_moh():
    """Asterisk die Wartemusik-Klassen neu einlesen lassen."""
    try:
        await ami.command("moh reload")
    except Exception:
        log.warning("Wartemusik: Asterisk konnte die Musik nicht neu laden")
