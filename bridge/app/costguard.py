"""Kostenbremse: begrenzt, wie oft und wie lange eine Rufnummer die KI nutzen kann (Schutz vor Dauer- oder Robo-Anrufen).

Je Agent (Agent-Editor): limit_calls_h = hoechstens so viele KI-Anrufe je Rufnummer in der letzten Stunde, limit_min_day =
hoechstens so viele KI-Minuten je Rufnummer seit Mitternacht. 0 = Standard aus den Einstellungen (meta limit_calls_h /
limit_min_day, dort 0 = aus). Global (Einstellungen): ai_min_day = hoechstens so viele KI-Minuten insgesamt pro Tag (0 = aus).
Ausgenommen: interne Nummern, Kontakte und Anrufe von den Fernabfrage-Nummern eines Benutzers (main). Externe Nummern der Benutzer
(Buero, ausser Haus) tragen die Benutzer selbst ein: sie werden wie jede andere Nummer gebremst. Anonyme Anrufer
(ohne Nummer) zaehlen nur fuer das Tageslimit insgesamt. Gezaehlt wird aus der Tabelle calls: Anrufe, bei denen die KI
gesprochen hat (Tokens/Kosten) oder die gerade mit der KI laufen. Ohne gesetzte Grenzen aendert sich nichts."""
import time

from . import db

# KI-Anruf = die Telefon-KI hat gesprochen (Sprachausgabe/Denken oder KI-Saetze im Transkript). Transkript- und Auswertungskosten
# eines Gespraechs zwischen Menschen zaehlen nicht (die schreiben nur Text-/Audio-Eingabe-Tokens und Kosten).
KI = "(tok_out_audio+tok_think > 0 OR EXISTS (SELECT 1 FROM segments s WHERE s.call_id=calls.id AND s.speaker='agent'))"


def _int(v):
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def settings():
    return {"limit_calls_h": _int(db.get_meta("limit_calls_h", "0")), "limit_min_day": _int(db.get_meta("limit_min_day", "0")),
            "ai_min_day": _int(db.get_meta("ai_min_day", "0"))}


def limits(agent):
    g = settings()
    return {"calls_h": _int((agent or {}).get("limit_calls_h")) or g["limit_calls_h"],
            "min_day": _int((agent or {}).get("limit_min_day")) or g["limit_min_day"], "ai_min_day": g["ai_min_day"]}


def _midnight():
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def exempt(caller):
    """Interne Nummer oder Kontakt: nie gebremst."""
    c = (caller or "").strip()
    d = db._digits(c)
    if c.startswith("**") or (d and len(d) <= 4):
        return True
    if not d:
        return False
    return bool(db.find_contact(c))


def _active_sql(active_ids):
    ids = [int(i) for i in active_ids or []]
    return (f" OR id IN ({','.join('?' * len(ids))})" if ids else ""), ids


def usage(caller, active_ids=()):
    """(KI-Anrufe dieser Nummer in der letzten Stunde, KI-Sekunden dieser Nummer heute, KI-Sekunden insgesamt heute)."""
    now, day = time.time(), _midnight()
    act, ids = _active_sql(active_ids)
    dur = "SUM(MAX(COALESCE(ended, ?) - started, 0))"
    total = db.q(f"SELECT {dur} s FROM calls WHERE started>=? AND direction!='rec' AND ({KI}{act})", (now, day, *ids))[0]["s"] or 0
    d = db._digits(caller)
    if not d:
        return 0, 0, total
    calls_h = db.q(f"SELECT COUNT(*) n FROM calls WHERE digits(caller)=? AND direction='in' AND started>=? AND ({KI}{act})",
                   (d, now - 3600, *ids))[0]["n"]
    secs = db.q(f"SELECT {dur} s FROM calls WHERE digits(caller)=? AND direction='in' AND started>=? AND ({KI}{act})",
                (now, d, day, *ids))[0]["s"] or 0
    return calls_h, secs, total


def check(agent, caller, active_ids=()):
    """None = die KI darf annehmen, sonst der Grund (fuer Protokoll und Anrufliste)."""
    lim = limits(agent)
    if not any(lim.values()) or exempt(caller):
        return None
    calls_h, secs, total = usage(caller, active_ids)
    if lim["ai_min_day"] and total >= lim["ai_min_day"] * 60:
        return f"Tageslimit der KI erreicht ({lim['ai_min_day']} Minuten insgesamt)"
    if not db._digits(caller):
        return None
    if lim["calls_h"] and calls_h >= lim["calls_h"]:
        return f"diese Nummer hat die KI in der letzten Stunde schon {calls_h}-mal erreicht (Grenze {lim['calls_h']})"
    if lim["min_day"] and secs >= lim["min_day"] * 60:
        return f"diese Nummer hat heute schon {round(secs / 60)} Minuten mit der KI gesprochen (Grenze {lim['min_day']} Minuten)"
    return None
