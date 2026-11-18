"""Zeitplan: Zeilen aus Tagen, Uhrzeit von-bis, Status und Freitext. Spaetere Zeilen ueberschreiben fruehere.

Status: office (im Buero/geoeffnet), away (abwesend), off (nicht erreichbar), closed (ausserhalb der Buerozeit).
Zeiten, die keine Zeile trifft, gelten als 'closed'. Die Zeitplaene gibt es global (Geschaeftszeiten) und pro Mitarbeiter."""
import json
import re

from . import hours, labels

STATUSES = labels.SCHEDULE_STATUS            # Texte: labels.py
NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _min(t):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(t or "").strip())
    if not m or int(m.group(1)) > 24 or int(m.group(2)) > 59 or (int(m.group(1)) == 24 and int(m.group(2))):
        raise ValueError(f"Ungültige Uhrzeit „{t}“")
    v = int(m.group(1)) * 60 + int(m.group(2))
    return 1440 if v >= 1439 else v          # 23:59 = bis Tagesende


def _fmt(m):
    return "24:00" if m >= 1440 else f"{m // 60:02d}:{m % 60:02d}"


def normalize(rules):
    """Prueft und bereinigt eine Regelliste (ValueError mit verstaendlicher Meldung)."""
    out = []
    for i, r in enumerate(rules or [], 1):
        days = sorted({int(d) for d in (r.get("days") or []) if str(d).isdigit() and 0 <= int(d) <= 6})
        if not days:
            raise ValueError(f"Zeile {i}: mindestens einen Tag wählen")
        a, b = _min(r.get("from")), _min(r.get("to"))
        if b <= a:
            raise ValueError(f"Zeile {i}: „bis“ muss nach „von“ liegen")
        st = r.get("status")
        if st not in STATUSES:
            raise ValueError(f"Zeile {i}: unbekannter Status")
        out.append({"days": days, "from": _fmt(a), "to": _fmt(b), "status": st, "note": (r.get("note") or "").strip()[:120]})
    return out


def dumps(rules):
    return json.dumps(normalize(rules), ensure_ascii=False) if rules else ""


def loads(text):
    if not (text or "").strip():
        return []
    try:
        return normalize(json.loads(text))
    except (ValueError, TypeError):
        return []


def from_legacy(text):
    """Alte Wochenzeit-Texte (z. B. 'Mo-Fr 08:00-17:00') als Zeilen mit Status 'im Buero'."""
    try:
        week = hours.parse(text)
    except ValueError:
        return []
    by_range = {}
    for d, rngs in week.items():
        for a, b in rngs:
            by_range.setdefault((a, b), []).append(d)
    return [{"days": sorted(d), "from": _fmt(a), "to": _fmt(b), "status": "office", "note": ""}
            for (a, b), d in sorted(by_range.items())]


def evaluate(rules, now):
    """Status zum Zeitpunkt now. matched=False: keine Zeile trifft zu -> 'closed'."""
    mins, day, hit = now.hour * 60 + now.minute, now.weekday(), None
    for r in rules or []:
        if day in r["days"] and _min(r["from"]) <= mins < _min(r["to"]):
            hit = r                              # spaetere Zeilen ueberschreiben fruehere
    if hit:
        return {"status": hit["status"], "note": hit["note"], "matched": True}
    return {"status": "closed", "note": "", "matched": False}


def describe(rules):
    if not rules:
        return "keine"
    out = []
    for r in rules:
        d = r["days"]
        days = ("täglich" if len(d) == 7 else NAMES[d[0]] + "–" + NAMES[d[-1]] if len(d) > 1 and d == list(range(d[0], d[-1] + 1))
                else ", ".join(NAMES[x] for x in d))
        out.append(f"{days} {r['from']}–{r['to']} {STATUSES[r['status']]}" + (f" ({r['note']})" if r["note"] else ""))
    return "; ".join(out)
