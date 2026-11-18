"""Geschaefts-/Arbeitszeiten als kurzer Text, z. B. "Mo-Fr 08:00-12:00,13:00-17:00; Sa 09:00-12:00".

Tage: Mo Di Mi Do Fr Sa So, Bereiche (Mo-Fr), Listen (Mo,Mi,Fr) oder "taeglich". Nicht genannte Tage sind geschlossen.
Zeiten: HH:MM-HH:MM (auch 8-17), mehrere Bereiche mit Komma, Ende immer nach Beginn (kein Mitternachtswechsel)."""
import re

DAYS = ["mo", "di", "mi", "do", "fr", "sa", "so"]
NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
LONG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _day(tok):
    t = tok.strip().lower().replace("ä", "ae")[:2]
    if t not in DAYS:
        raise ValueError(f"Unbekannter Tag „{tok.strip()}“ (erlaubt: Mo Di Mi Do Fr Sa So)")
    return DAYS.index(t)


def _time(tok):
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", tok.strip())
    if not m or int(m.group(1)) > 24 or int(m.group(2) or 0) > 59:
        raise ValueError(f"Ungültige Uhrzeit „{tok.strip()}“ (Format 08:00)")
    return int(m.group(1)) * 60 + int(m.group(2) or 0)


def parse(text):
    """-> {Wochentag 0-6: [(von_min, bis_min), ...]}; ValueError mit verstaendlicher Meldung."""
    week = {}
    for seg in re.split(r"[;\n]", text or ""):
        seg = seg.strip()
        if not seg:
            continue
        m = re.match(r"^([^\d]+?)\s*(\d.*)$", seg)
        if not m:
            raise ValueError(f"„{seg}“: Tage und Zeiten fehlen (Beispiel: Mo-Fr 08:00-17:00)")
        dpart, tpart = m.group(1).strip().replace("–", "-"), m.group(2)
        days = set()
        if re.fullmatch(r"(t[aä]glich|alle|jeden tag)", dpart.lower()):
            days = set(range(7))
        else:
            for part in dpart.split(","):
                if "-" in part:
                    a, b = (_day(x) for x in part.split("-", 1))
                    days |= set(range(a, b + 1)) if a <= b else set(range(a, 7)) | set(range(0, b + 1))
                elif part.strip():
                    days.add(_day(part))
        ranges = []
        for r in tpart.replace("–", "-").split(","):
            if not r.strip():
                continue
            if "-" not in r:
                raise ValueError(f"Zeitbereich „{r.strip()}“ braucht von-bis (z. B. 08:00-17:00)")
            a, b = (_time(x) for x in r.split("-", 1))
            if b <= a:
                raise ValueError(f"Zeitbereich „{r.strip()}“: Ende muss nach dem Beginn liegen")
            ranges.append((a, b))
        if not ranges:
            raise ValueError(f"„{seg}“: keine Zeiten angegeben")
        for d in days:
            week.setdefault(d, []).extend(ranges)
    return week


def is_open(text, now):
    """True/False; None wenn keine Zeiten hinterlegt sind (dann gilt: keine Einschraenkung)."""
    if not (text or "").strip():
        return None
    mins = now.hour * 60 + now.minute
    return any(a <= mins < b for a, b in parse(text).get(now.weekday(), []))


def _fmt(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def describe(text):
    """Normalisierte Darstellung, aufeinanderfolgende gleiche Tage zusammengefasst."""
    week = parse(text)
    if not week:
        return "keine"
    rows = [(d, ", ".join(f"{_fmt(a)}–{_fmt(b)}" for a, b in sorted(set(week[d])))) for d in sorted(week)]
    out, i = [], 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1][0] == rows[j][0] + 1 and rows[j + 1][1] == rows[i][1]:
            j += 1
        days = NAMES[rows[i][0]] if i == j else f"{NAMES[rows[i][0]]}–{NAMES[rows[j][0]]}"
        out.append(f"{days} {rows[i][1]}")
        i = j + 1
    return "; ".join(out)
