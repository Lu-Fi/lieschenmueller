"""Diagnose vermittelter Gespraeche (Fehlersuche, z. B. "Mitarbeiter hoert nach dem Abnehmen ein Freizeichen").

1. Seitenpruefung: Vor dem Loeschen der Einzelspuren (peer = Gegenseite, me = Mitarbeiter) wird je Seite gezaehlt, wie viele
   Sekunden Sprache, Stille und ein 425-Hz-Ton (deutsches Frei-/Besetztzeichen) enthalten sind. Ein solcher Ton NACH dem Abnehmen
   kommt nie von Asterisk (Wartemusik/Freizeichen enden mit der Annahme), sondern von der Leitung bzw. dem Telefon dieser Seite.
2. Leitungswerte: Beim Auflegen meldet der Dialplan je Kanal die RTCP-Werte von Asterisk (/internal/qos): empfangene und
   gesendete Pakete, Verluste, Jitter, Laufzeit. 0 empfangene Pakete = von dieser Seite kam gar kein Ton an.

Die Ergebnisse stehen als Ereignisse (line, qos) im Ablauf des Anrufs. Es wird nichts gespeichert, was nicht ohnehin aufgenommen wurde."""
import logging

import numpy as np

log = logging.getLogger("linediag")

RATE = 8000
WIN = 4000                 # 0,5 s je Fenster
TONE_SHARE = 0.6           # Anteil des 425-Hz-Bandes am Spektrum, ab dem ein Fenster als Ton gilt
SPEECH_RMS = 400           # Pegel, ab dem ein Fenster (ohne Ton) als Sprache gilt
SILENCE_RMS = 120


def analyze(pcm):
    """Spur (8 kHz, mono, 16 Bit) -> {"dur", "speech", "tone", "silence", "tone_first"} in Sekunden."""
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")        # nur eine Sicht auf die Bytes, keine Kopie der ganzen Spur
    out = {"dur": round(len(x) / RATE, 1), "speech": 0.0, "tone": 0.0, "silence": 0.0, "tone_first": None}
    if len(x) < WIN:
        return out
    freq = np.fft.rfftfreq(WIN, 1 / RATE)
    band = (freq > 412) & (freq < 438)
    win = np.hanning(WIN)
    step = WIN / RATE
    for i in range(0, len(x) - WIN + 1, WIN):
        s = x[i:i + WIN].astype(np.float64)          # Fenster fuer Fenster umwandeln (lange Gespraeche: wenig Speicher)
        rms = float(np.sqrt((s ** 2).mean()))
        if rms < SILENCE_RMS:
            out["silence"] += step
            continue
        sp = np.abs(np.fft.rfft(s * win))
        if sp.sum() > 0 and sp[band].sum() / sp.sum() >= TONE_SHARE:
            out["tone"] += step
            if out["tone_first"] is None:
                out["tone_first"] = round(i / RATE, 1)
        elif rms >= SPEECH_RMS:
            out["speech"] += step
    for k in ("speech", "tone", "silence"):
        out[k] = round(out[k], 1)
    return out


def side_text(name, a):
    t = f"{name}: {a['speech']:g} s Sprache"
    if a["tone"]:
        t += f", {a['tone']:g} s Freizeichen/Besetztton (425 Hz, ab {a['tone_first']:g} s)"
    return t + f", {a['silence']:g} s Stille"


def warnings(sides):
    """Auffaelligkeiten fuer den Ablauf: Ton nach dem Abnehmen oder eine Seite ganz ohne Sprache."""
    out = []
    for name, a in sides.items():
        if a["tone"] >= 1:
            out.append(f"{name}: Freizeichen/Besetztton nach dem Abnehmen – kommt von dieser Leitung/diesem Telefon, nicht von der Anlage")
        elif a["dur"] >= 5 and a["speech"] < 0.5:
            out.append(f"{name}: keine Sprache – Ton dieser Seite kam evtl. nicht an")
    return out


QOS_KEYS = ("rxcount", "txcount", "lp", "rlp", "rxjitter", "txjitter", "rtt", "rxmes", "txmes")


def parse_qos(q):
    """RTCP-Zusammenfassung von Asterisk (CHANNEL(rtcp,all): 'ssrc=..;themssrc=..;lp=0;rxjitter=0.0;rxcount=123;...') -> dict."""
    out = {}
    for part in str(q or "").split(";"):
        k, _, v = part.partition("=")
        k = k.strip()
        if k in QOS_KEYS:
            try:
                out[k] = float(v) if "." in v else int(v)
            except ValueError:
                pass
    return out


def qos_text(leg, d):
    who = {"a": "eingehende Leitung", "b": "angerufenes Telefon"}.get(leg, leg)
    t = f"Leitungswerte {who}: {d.get('rxcount', '?')} Pakete empfangen, {d.get('txcount', '?')} gesendet"
    if d.get("lp") or d.get("rlp"):
        t += f", verloren {d.get('lp', 0)} (bei uns) / {d.get('rlp', 0)} (Gegenseite)"
    if d.get("rxjitter"):
        t += f", Jitter {d['rxjitter']:g}"
    if d.get("rxcount") == 0:
        t += " – von dieser Seite kam kein Ton an"
    return t
