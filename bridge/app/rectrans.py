"""Gespraeche zwischen Menschen: Aufnahme (Asterisk MixMonitor, getrennte Spuren) -> Nachtranskription.

Die Spuren landen in data/rec-in (Asterisk: /recordings). -me = Nutzer, -peer = Gegenseite.
Nach der Transkription werden die Audiodateien geloescht (Datenminimierung); ist in den Einstellungen "Audio dauerhaft speichern"
eingeschaltet, bleibt die gemischte Aufnahme erhalten und ist in der Anrufliste abspielbar."""
import asyncio
import contextlib
import audioop
import io
import json
import logging
import os
import re
import time
import wave

from google import genai
from google.genai import types

from . import db, gclient, linediag, models, mp3, voices
from .audio import Down3
from .events import hub

log = logging.getLogger("rectrans")
_base = os.path.dirname(os.environ.get("DB_PATH", "/data/lieschen.db"))
REC_IN = os.path.join(_base, "rec-in")
SOUNDS = os.path.join(_base, "sounds")
NOTICE_TEXT = "Hinweis: Dieses Gespräch wird aufgezeichnet und transkribiert."
PROMPT = ("Transkribiere dieses Telefon-Audio wörtlich auf Deutsch (es spricht nur eine Person). "
          "Gib jede Äußerung in einer eigenen Zeile im Format [mm:ss] Text aus. Keine Zusammenfassung, "
          "keine Kommentare, nichts hinzufügen. Wenn keine Sprache hörbar ist, antworte exakt mit: [keine Sprache]")
PROMPT_MIX = ("Transkribiere dieses Telefongespräch wörtlich auf Deutsch. Es sprechen zwei Personen, auch gleichzeitig oder ineinander. "
              "Gib jede Äußerung in einer eigenen Zeile im Format [mm:ss] Text aus, beide Seiten vollständig, auch lange Beiträge und kurze Einwürfe. "
              "Keine Zusammenfassung, keine Kommentare, nichts hinzufügen. Wenn keine Sprache hörbar ist, antworte exakt mit: [keine Sprache]")
LINE = re.compile(r"^\[(\d{1,3}):(\d{2})\]\s*(.+)$")
SUFFIXES = (("local", "-me.wav"), ("remote", "-peer.wav"))
RUNNING = set()
VOICE_STOP = None               # async fn(call_id, uuid, wer, text), setzt main.py
_Q = r"(aufzeichnung|aufnahme|mitschnitt)"
STOP_PHRASES = [re.compile(p, re.I) for p in (
    r"lieschen\W+(bitte\s+)?(keine\s+(weitere\s+)?" + _Q + r"|" + _Q + r"\s+(beenden|stoppen|aus|ausschalten))",
    r"(bitte\s+)?" + _Q + r"\s+(bitte\s+)?(beenden|stoppen|ausschalten|abbrechen)",
    r"(bitte\s+nicht|^\W*nicht)\s+(aufzeichnen|aufnehmen|mitschneiden)",
    r"^\W*(bitte\s+)?keine\s+" + _Q + r"\W*$",
    r"(hätte|möchte|will|wünsche|würde|moechte|haette)\s+(\w+\s+){0,2}keine\s+" + _Q)]


def stop_requested(text):
    return any(p.search(text or "") for p in STOP_PHRASES)


def files(uuid):
    return {k: os.path.join(REC_IN, uuid + s) for k, s in SUFFIXES} | {"mix": os.path.join(REC_IN, uuid + "-mix.wav")}


async def ensure_notice():
    """Feste Hinweis-Ansage fuer den Angerufenen als Asterisk-Sounddatei (sln/alaw/ulaw, 8 kHz)."""
    base = os.path.join(SOUNDS, "rec_notice")
    if all(os.path.exists(base + e) for e in (".sln", ".alaw", ".ulaw")) and db.get_meta("rec_notice_v", "") == "2":
        return
    wav = await voices.sample("Kore", NOTICE_TEXT, None, compact=True)
    with wave.open(io.BytesIO(wav)) as w:
        pcm = w.readframes(w.getnframes())
    pcm8 = bytes(16000) + Down3()(pcm)        # 0,5 s Stille vorweg (8 kHz, 16 Bit): der Anfang wird sonst beim Zuschalten verschluckt
    os.makedirs(SOUNDS, exist_ok=True)
    for ext, data in ((".sln", pcm8), (".alaw", audioop.lin2alaw(pcm8, 2)), (".ulaw", audioop.lin2ulaw(pcm8, 2))):
        with open(base + ext, "wb") as f:
            f.write(data)
        os.chmod(base + ext, 0o644)
    db.set_meta("rec_notice_v", "2")             # Version 2: 0,5 s Stille vorweg


def _read_pcm(path):
    """PCM-Daten einer MixMonitor-WAV (8 kHz, mono, 16 Bit). Funktioniert auch, wenn Asterisk die Datei noch nicht
    abgeschlossen hat und im Kopf noch '0 Daten' steht: dann werden die Bytes nach dem 44-Byte-Kopf genommen."""
    try:
        b = open(path, "rb").read()
    except OSError:
        return b""
    if len(b) <= 44:
        return b""
    try:
        with wave.open(io.BytesIO(b)) as w:
            if w.getnframes() > 0:
                return w.readframes(w.getnframes())
    except Exception:
        pass
    return b[44:44 + (len(b) - 44) // 2 * 2]


def _wav_bytes(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(pcm)
    return buf.getvalue()


def _info(pcm):
    """(Dauer in s, Pegel)"""
    return len(pcm) / 16000, audioop.rms(pcm, 2) if pcm else 0


async def _wait_stable(paths, quiet=2.0, timeout=30.0):
    """Asterisk meldet das Ende, bevor es die Dateien geschlossen hat: warten, bis sie nicht mehr wachsen."""
    import asyncio
    t0, last, since = time.time(), None, time.time()
    while time.time() - t0 < timeout:
        sizes = [os.path.getsize(p) if os.path.exists(p) else -1 for p in paths]
        if sizes != last:
            last, since = sizes, time.time()
        elif time.time() - since >= quiet:
            return
        await asyncio.sleep(0.5)


async def _ask(client, data, prompt=PROMPT):
    r = await client.aio.models.generate_content(
        model=models.transcribe_model(db.get_meta("transcribe_model", ""))[0],
        contents=[types.Part.from_bytes(data=data, mime_type="audio/wav"), prompt])
    return r.text or "", r.usage_metadata


def _lines(text, who):
    out = []
    for raw in text.splitlines():
        m = LINE.match(raw.strip())
        if m:
            out.append((int(m.group(1)) * 60 + int(m.group(2)), who, m.group(3).strip()))
    return out


def _cost(tok):
    p = models.transcribe_model(db.get_meta("transcribe_model", ""))[1]
    return (tok["in_text"] * p["in_text"] + tok["in_audio"] * p["in_audio"] + tok["out_text"] * p["out_text"]) / 1e6


LIVE = {}                     # uuid -> Task der Live-Schleife
LIVE_INTERVAL = float(os.environ.get("LIVE_INTERVAL", "10"))
LIVE_MAX_S = 3 * 3600


def _read_from(path, offset):
    """Neue PCM-Bytes ab Offset (nach dem 44-Byte-Kopf); funktioniert auch waehrend Asterisk noch schreibt."""
    try:
        with open(path, "rb") as f:
            f.seek(44 + offset)
            b = f.read()
    except OSError:
        return b""
    return b[:len(b) // 2 * 2]


def _cut_point(pcm, force):
    """Schnittstelle an einer Sprechpause (>= 0,4 s still), damit kein Wort halbiert wird. 0 = noch warten."""
    win = 1600                                  # 100 ms bei 8 kHz, 16 Bit
    quiet = [audioop.rms(pcm[i:i + win], 2) < 250 for i in range(0, len(pcm) - win + 1, win)]
    for i in range(len(quiet) - 4, 3, -1):      # letzte Pause, die nicht ganz am Ende liegt
        if all(quiet[i:i + 4]):
            return i * win + 2 * win           # mitten in der Pause schneiden
    return len(pcm) if force else 0


async def live_loop(call_id, uuid, base, store=True):
    """Waehrend des Gespraechs: etwa alle 10 s neue Stuecke je Seite transkribieren und sofort anzeigen.
    Am Ende ersetzt die Gesamt-Transkription (transcribe) diese Zwischenstaende."""
    client = gclient.client(genai, "text")
    f = files(uuid)
    offsets = {"local": 0, "remote": 0}
    t_end = time.time() + LIVE_MAX_S
    try:
        while time.time() < t_end:
            await asyncio.sleep(LIVE_INTERVAL)
            new = []
            for who, _ in SUFFIXES:
                chunk = _read_from(f[who], offsets[who])
                if len(chunk) < 4 * 16000:       # unter 4 s: noch sammeln
                    continue
                cut = _cut_point(chunk, force=len(chunk) > 25 * 16000)
                if not cut:
                    continue
                piece, start = chunk[:cut], offsets[who] / 16000
                offsets[who] += cut
                dur, level = _info(piece)
                if dur < 1.5 or level < 120:
                    continue
                try:
                    text, u = await _ask(client, _wav_bytes(piece))
                except Exception as e:
                    log.warning("Live-Transkription (%s) fehlgeschlagen: %s", who, type(e).__name__)
                    offsets[who] -= cut          # beim naechsten Durchlauf erneut versuchen
                    continue
                new += [(secs + start, w, t) for secs, w, t in _lines(text, who) if not _no_speech(t)]
                tok = {"in_text": 0, "in_audio": 0, "out_text": 0}
                for d in (u.prompt_tokens_details or []) if u else []:
                    tok["in_audio" if "AUDIO" in str(d.modality).upper() else "in_text"] += d.token_count or 0
                tok["out_text"] += ((u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)) if u else 0
                db.x("UPDATE calls SET tok_in_text=tok_in_text+?, tok_in_audio=tok_in_audio+?, tok_out_text=tok_out_text+?, "
                     "cost_usd=cost_usd+? WHERE id=?", (tok["in_text"], tok["in_audio"], tok["out_text"], _cost(tok), call_id))
            for secs, who, text in sorted(new):
                ts = base + secs
                if store:
                    db.x("INSERT INTO segments(call_id,ts,speaker,text,agent) VALUES(?,?,?,?,?)", (call_id, ts, who, text, ""))
                    hub.publish({"type": "segment", "call_id": call_id, "speaker": who, "text": text, "ts": ts, "agent": ""})
            if VOICE_STOP and db.q("SELECT 1 FROM calls WHERE id=? AND rec_voicestop=1", (call_id,)):
                for secs, who, text in sorted(new):
                    if stop_requested(text):
                        await VOICE_STOP(call_id, uuid, who, text)
                        return
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("Live-Schleife abgebrochen")


def start_live(call_id, uuid, base, store=True):
    import asyncio
    t = LIVE.get(uuid)
    if t is None or t.done():                   # nach einem Stopp (Sprachbefehl/Knopf) darf eine neue Aufzeichnung wieder starten
        LIVE[uuid] = asyncio.create_task(live_loop(call_id, uuid, base, store))


async def stop_live(uuid):
    t = LIVE.pop(uuid, None)
    if t:
        t.cancel()
        try:
            await t
        except BaseException:
            pass


def _diagnose(call_id, f):
    """Seitenpruefung der Einzelspuren (Sprache, Stille, 425-Hz-Ton) als Ereignis "line" im Ablauf, bevor die Spuren geloescht werden."""
    try:
        sides = {}
        for key, name in (("remote", "Gegenseite"), ("local", "Mitarbeiter")):
            pcm = _read_pcm(f[key])
            if len(pcm) >= 16000:            # ab 1 s
                sides[name] = linediag.analyze(pcm)
        if sides:
            from . import flow
            flow.add(call_id, "line", sides=sides, warn=linediag.warnings(sides))
    except Exception:
        log.exception("Seitenpruefung von Anruf %s fehlgeschlagen", call_id)


def finish_audio(call_id, uuid):
    """Audio nach dem Gespraech: gemischte Aufnahme dauerhaft ablegen (wenn eingeschaltet), sonst loeschen; Einzelspuren immer loeschen."""
    f = files(uuid)
    _diagnose(call_id, f)
    row = db.q("SELECT rec_keep FROM calls WHERE id=?", (call_id,))
    rk = row[0]["rec_keep"] if row else -1      # 1/0 = Entscheidung des Agenten, -1 = Einstellung "Audio dauerhaft speichern"
    keep = (rk == 1 or (rk == -1 and db.get_meta("keep_audio", "0") == "1")) and os.path.exists(f["mix"]) and os.path.getsize(f["mix"]) > 2000
    if keep:
        rec_dir = os.path.join(_base, "rec")
        os.makedirs(rec_dir, mode=0o700, exist_ok=True)
        have = db.q("SELECT recording FROM calls WHERE id=?", (call_id,))
        taken = bool(have and have[0]["recording"] and os.path.exists(have[0]["recording"]))
        dest = os.path.join(rec_dir, f"call-{call_id}{'-gespraech' if taken else ''}.wav")     # nicht die KI-Aufnahme desselben Anrufs ueberschreiben
        try:
            if not mp3.wait_wav(f["mix"]):
                log.warning("Aufnahme von Anruf %s: Datei nicht lesbar (Asterisk schreibt noch?)", call_id)
            os.replace(f["mix"], dest)
            if taken:
                # KI-Aufnahme (Anfang) und Gespraech zu EINER Aufnahme zusammenfuegen
                try:
                    out = os.path.join(rec_dir, f"call-{call_id}.wav")
                    mp3.append_audio(have[0]["recording"], dest, out)
                    from . import flow
                    flow.add(call_id, "rec", what="conversation")
                    new = mp3.to_mp3_inplace(out)
                    db.x("UPDATE calls SET recording=? WHERE id=?", (new, call_id))
                    os.remove(dest)
                    dest = None
                except Exception:
                    log.exception("Aufnahmen von Anruf %s nicht zusammengefuegt - das Gespraech bleibt als eigene Datei", call_id)
                    dest = mp3.to_mp3_inplace(dest)
            else:
                dest = mp3.to_mp3_inplace(dest)
                db.x("UPDATE calls SET recording=? WHERE id=? AND recording=''", (dest, call_id))
        except OSError:
            log.exception("Aufnahme ablegen fehlgeschlagen")
    for p in f.values():
        if os.path.exists(p):
            os.remove(p)


CHUNK_S = 120             # Sekunden je Anfrage bei langen Spuren (kuerzere Stuecke: das Modell laesst sonst bei fast stillen Spuren ganze Beitraege aus)
MIN_WORDS_PER_SPEECH_S = 0.8     # weniger Woerter je Sekunde Sprache = verdaechtig wenig (normales Sprechen: 2 bis 3)
MAX_WORDS_PER_SPEECH_S = 5.0     # mehr ist nicht sprechbar: erfundener Text


def _collapse_runs(lines, keep=2, limit=6):
    """Endlosschleifen des Modells ('Mhm.' hundertfach): besteht eine Folge aus mindestens `limit` gleichen Zeilen, bleiben nur `keep`."""
    out, i = [], 0
    while i < len(lines):
        j = i
        while j < len(lines) and lines[j][2] == lines[i][2]:
            j += 1
        out += lines[i:j] if j - i < limit else lines[i:i + keep]
        i = j
    return out


def _speech_seconds(pcm):
    """Sekunden mit Sprache (100-ms-Fenster mit Pegel ab 400)."""
    win = 1600
    return sum(1 for i in range(0, len(pcm) - win + 1, win) if audioop.rms(pcm[i:i + win], 2) >= 400) * 0.1


async def _transcribe_piece(client, piece, start, who, tok, depth=0):
    """Ein Stueck transkribieren. Kommt auffaellig wenig Text heraus (Modell hat Beitraege ausgelassen), wird das Stueck an einer Sprechpause
    geteilt und die Haelften einzeln transkribiert; es gilt die Fassung mit mehr Text. Rueckgabe: Zeilen."""
    dur, level = _info(piece)
    if dur < 1.5 or level < 120:                  # leer/zu kurz: gar nicht erst senden (Modell halluziniert sonst)
        return []
    text, u = await _ask(client, _wav_bytes(piece), PROMPT_MIX if who == "mix" else PROMPT)
    for d in (u.prompt_tokens_details or []) if u else []:
        tok["in_audio" if "AUDIO" in str(d.modality).upper() else "in_text"] += d.token_count or 0
    tok["out_text"] += ((u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)) if u else 0
    # "[keine Sprache]" gilt nur fuer die Zeile, in der es steht (das Modell setzt es auch zwischen echte Zeilen)
    lines = [(s + start, w, t) for s, w, t in _lines(text, who) if s <= dur + 3 and not _no_speech(t)]     # nichts hinter dem Ende
    lines = _collapse_runs(lines)
    words, speech = sum(len(t.split()) for _, _, t in lines), _speech_seconds(piece)
    most = max(speech, 2.0) * MAX_WORDS_PER_SPEECH_S         # mehr ist nicht sprechbar (erfunden/Schleife)
    few, many = dur >= 40 and words < speech * MIN_WORDS_PER_SPEECH_S, words > most
    if depth < 2 and (few or (many and dur >= 20)):
        mid = _cut_point(piece[int(dur * 0.4) * 16000:int(dur * 0.6) * 16000], force=True)
        cut = int(dur * 0.4) * 16000 + mid
        log.info("Transkription: %d Woerter bei %.0f s Sprache (ab %.0f s, %s) - Stueck wird geteilt", words, speech, start,
                 "zu wenig" if few else "mehr als sprechbar")
        retry = (await _transcribe_piece(client, piece[:cut], start, who, tok, depth + 1)
                 + await _transcribe_piece(client, piece[cut:], start + cut / 16000, who, tok, depth + 1))
        retry = _collapse_runs(retry)
        n = sum(len(t.split()) for _, _, t in retry)
        if few and words < n <= most:          # mehr Text, aber nicht mehr als Sprechen moeglich ist
            return retry
        if many and n < words:                 # erfundener Text: die kuerzere Fassung gilt
            return retry
    elif many:
        log.warning("Transkription: %d Woerter bei nur %.0f s Sprache (ab %.0f s) - vermutlich teilweise erfunden", words, speech, start)
    return lines


def _no_speech(t):
    return t.strip().strip(".").lower() in ("[keine sprache]", "keine sprache")


async def _transcribe_track(client, pcm, who):
    """Eine Spur transkribieren. Lange Spuren werden an Sprechpausen in Stuecke von etwa 2 Minuten geteilt: bei langen Dateien
    erfindet das Modell sonst Zeilen und Zeitmarken hinter dem Ende oder laesst Beitraege aus. Rueckgabe: (Zeilen, Tokens)."""
    bps = 16000                                  # Bytes je Sekunde (8 kHz, 16 Bit)
    tok = {"in_text": 0, "in_audio": 0, "out_text": 0}
    lines, pos = [], 0
    while pos < len(pcm):
        if len(pcm) - pos > (CHUNK_S + 15) * bps:
            tail = pcm[pos + (CHUNK_S - 10) * bps: pos + (CHUNK_S + 10) * bps]
            end = pos + (CHUNK_S - 10) * bps + _cut_point(tail, force=True)
        else:
            end = len(pcm)
        lines += await _transcribe_piece(client, pcm[pos:end], pos / bps, who, tok)
        pos = end
    return _collapse_runs(lines), tok


async def retranscribe_from_recording(call_id, backup_dir=None):
    """Transkript aus der abgelegten Aufnahme (MP3, ein Mix beider Seiten) neu erstellen, z. B. wenn das gespeicherte Transkript Luecken hat.
    Ohne Sprechertrennung: die Zeilen stehen als Sprecher 'mix'. Das alte Transkript wird immer vorher als JSON gesichert (Standard: <Datenordner>/backups).
    Nur fuer Anrufe ohne KI-Anteil (die Aufnahme enthaelt sonst auch den KI-Teil). Ersetzt nur, wenn die neue Fassung mehr Text hat.
    Rueckgabe: (alte, neue Wortzahl, ersetzt)."""
    r = db.q("SELECT recording, started FROM calls WHERE id=?", (call_id,))
    if not r or not r[0]["recording"] or not os.path.exists(r[0]["recording"]):
        raise FileNotFoundError("keine Aufnahme zu diesem Anruf")
    if db.q("SELECT 1 FROM segments WHERE call_id=? AND speaker='agent' LIMIT 1", (call_id,)):
        raise ValueError("Anruf mit KI-Anteil: die Aufnahme enthaelt auch den KI-Teil")
    pcm = await asyncio.to_thread(mp3.mp3_to_pcm8k, r[0]["recording"])
    client = gclient.client(genai, "text")
    lines, tok = await _transcribe_track(client, pcm, "mix")
    db.x("UPDATE calls SET tok_in_audio=tok_in_audio+?, tok_in_text=tok_in_text+?, tok_out_text=tok_out_text+?, cost_usd=cost_usd+? WHERE id=?",
         (tok["in_audio"], tok["in_text"], tok["out_text"], _cost(tok), call_id))
    old = db.q("SELECT * FROM segments WHERE call_id=? AND speaker IN ('local','remote','mix') ORDER BY ts, id", (call_id,))
    old_words, new_words = sum(len(x["text"].split()) for x in old), sum(len(t.split()) for _, _, t in lines)
    if new_words <= old_words:
        return old_words, new_words, False
    backup_dir = backup_dir or os.path.join(_base, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    with open(os.path.join(backup_dir, f"call-{call_id}-segments-{int(time.time())}.json"), "w", encoding="utf-8") as f:
        json.dump([dict(x) for x in old], f, ensure_ascii=False)
    base = (old[0]["ts"] - lines[0][0]) if old and lines else float(r[0]["started"])     # erste neue Zeile an der Stelle der ersten alten
    c = db.conn()
    with db._lock if hasattr(db, "_lock") else contextlib.nullcontext():
        c.execute("BEGIN")
        try:
            c.execute("DELETE FROM segments WHERE call_id=? AND speaker IN ('local','remote','mix')", (call_id,))
            c.executemany("INSERT INTO segments(call_id,ts,speaker,text,agent) VALUES(?,?,?,?,?)",
                          [(call_id, float(base + secs), "mix", text, "") for secs, _, text in lines])
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    hub.publish({"type": "end", "call_id": call_id})
    return old_words, new_words, True


async def transcribe(call_id, uuid):
    """Spuren transkribieren, Saetze zusammenfuehren und speichern. Bei Fehler bleiben die Dateien liegen."""
    if uuid in RUNNING:
        return
    RUNNING.add(uuid)
    try:
        await stop_live(uuid)
        db.x("UPDATE calls SET status='transkribiere' WHERE id=?", (call_id,))
        hub.publish({"type": "call", "call_id": call_id, "status": "transkribiere"})
        client = gclient.client(genai, "text")
        f = files(uuid)
        await _wait_stable([f["local"], f["remote"], f["mix"]])
        lines, tok, spoke, audio_end = [], {"in_text": 0, "in_audio": 0, "out_text": 0}, False, 0.0
        for who, _ in SUFFIXES:
            pcm = _read_pcm(f[who])
            dur, level = _info(pcm)
            log.info("Spur %s: %.1f s, Pegel %d", who, dur, level)
            if dur < 1.5 or level < 120:        # leer/zu kurz: gar nicht erst senden (Modell halluziniert sonst)
                continue
            spoke = True
            track_lines, tk = await _transcribe_track(client, pcm, who)
            lines += track_lines
            for k in tok:
                tok[k] += tk[k]
            audio_end = max(audio_end, dur)
        call = db.q("SELECT started, direction FROM calls WHERE id=?", (call_id,))[0]
        had_base = bool(db.get_meta(f"recbase:{uuid}", ""))       # es wurde ein Gespraech mit einer Person aufgenommen
        base = float(db.get_meta(f"recbase:{uuid}", "") or call["started"])   # Beginn der Aufnahme
        db.x("DELETE FROM meta WHERE key=?", (f"recbase:{uuid}",))
        lines.sort()
        # Live-Zwischenstaende durch die Gesamt-Transkription ersetzen (keine halbierten Woerter, richtige Reihenfolge)
        # nur ab Beginn der Aufnahme: die Antwort des Benutzers bei der Rueckfrage (speaker local, vorher) bleibt stehen
        # ohne aufgenommenes Gespraech (z. B. Rueckfrage abgelehnt) bleibt alles stehen
        if had_base or lines:
            db.x("DELETE FROM segments WHERE call_id=? AND speaker IN ('local','remote','mix') AND ts>=?", (call_id, base - 1))
        db.x("DELETE FROM segments WHERE call_id=? AND speaker='system' AND text='Mitschrift des vermittelten Gesprächs'", (call_id,))
        if call["direction"] != "rec" and lines:
            db.add_segment(call_id, "system", "Mitschrift des vermittelten Gesprächs")
        for secs, who, text in lines:
            db.x("INSERT INTO segments(call_id,ts,speaker,text,agent) VALUES(?,?,?,?,?)",
                 (call_id, base + secs, who, text, ""))
        if not lines:
            db.add_segment(call_id, "system", "Keine Sprache erkannt" if spoke else
                           "Keine Sprache aufgezeichnet (Gegenseite nicht erreicht?)")
        # Kosten der Transkription zu den vorhandenen (KI-Gespraech) addieren; Endstatus je nach Anrufart
        db.x("UPDATE calls SET tok_in_text=tok_in_text+?, tok_in_audio=tok_in_audio+?, tok_out_text=tok_out_text+?, "
             "cost_usd=cost_usd+?, ended=COALESCE(ended, ?), "
             "status=? WHERE id=?",
             (tok["in_text"], tok["in_audio"], tok["out_text"], _cost(tok), base + audio_end if audio_end else time.time(), db.end_status(call_id), call_id))
        await asyncio.to_thread(finish_audio, call_id, uuid)        # MP3-Kodierung/Zusammenfuegen blockiert sonst laufende Gespraeche
        hub.publish({"type": "end", "call_id": call_id})
    except Exception as e:
        log.exception("Transkription fehlgeschlagen")
        db.x("UPDATE calls SET status='transkription fehlgeschlagen' WHERE id=?", (call_id,))
        db.add_segment(call_id, "system", f"Transkription fehlgeschlagen ({type(e).__name__}) - Aufnahme bleibt erhalten, "
                                          "erneut versuchen im Anruf")
        hub.publish({"type": "end", "call_id": call_id})
    finally:
        RUNNING.discard(uuid)
