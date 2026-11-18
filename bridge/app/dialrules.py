"""Waehlregeln eines Mitarbeiters: je nach Anrufer, Tag/Uhrzeit und angerufener Nummer gilt ein anderer Status.

Jede Regel hat einen Ein/Aus-Schalter. Die erste passende aktive Regel gewinnt; passt keine, gelten Zeitplan und manueller
Status. "Nicht stoeren" geht den Regeln vor (sie gelten dann nur fuer die Ausnahmen); die ganze Rangfolge steht in
db.RESOLVE_RULES, ausgewertet von db.resolve(). Aktionen:
  office  im Buero erreichbar (Buero-Nummer)        away  abwesend (Nummer ausser Haus, sonst Vertretung)
  off     nicht erreichbar (Vertretung, sonst KI)    forward  an eine bestimmte Nummer
  ai      direkt zum Anrufbeantworter (KI)"""
import re
import secrets

from . import labels
from . import schedule

MODES = labels.DIALRULE_MODES                 # Texte: labels.py
ACTIONS = labels.DIALRULE_ACTIONS
DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MAX_RULES = 30


def _digits(t):
    return "".join(c for c in str(t or "") if c.isdigit())


def _nat(n):
    """Ziffern in nationale Schreibweise (+49 171 / 0049 171 / 49171 -> 0171), damit Schreibweisen gleich vergleichbar sind;
    andere Laender mit + wie mit 00 (+43 1 -> 00431)."""
    s, d = str(n or "").strip(), _digits(n)
    if s.startswith("+") and not d.startswith("49"):
        return "00" + d
    if s.startswith("+"):
        return "0" + d[2:]
    if d.startswith("0049"):
        return "0" + d[4:]
    if d.startswith("49") and len(d) >= 10:
        return "0" + d[2:]
    return d


def _min(t, label):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(t).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"{label}: ungültige Uhrzeit „{t}“")
    return int(m.group(1)) * 60 + int(m.group(2))


def normalize(rules, dial_number):
    """Prueft und bereinigt die Regelliste (ValueError mit verstaendlicher Meldung). dial_number prueft Ziele."""
    if len(rules or []) > MAX_RULES:
        raise ValueError(f"Höchstens {MAX_RULES} Regeln")
    out = []
    for i, r in enumerate(rules or [], 1):
        lab = f"Regel {i}"
        mode, action = r.get("caller_mode", "any"), r.get("action")
        if mode not in MODES:
            raise ValueError(f"{lab}: unbekannte Anrufer-Auswahl")
        if action not in ACTIONS:
            raise ValueError(f"{lab}: unbekannte Aktion")
        callers = []
        if mode == "list":
            raw = r.get("callers") or []
            if isinstance(raw, str):
                raw = re.split(r"[,;\n]", raw)
            # Stern am Ende = alle Nummern, die so beginnen (z. B. 0172555*)
            callers = [_nat(c) + ("*" if str(c).strip().endswith("*") else "") for c in raw if str(c).strip()]
            if not callers or any(len(_digits(c)) < 3 for c in callers):
                raise ValueError(f"{lab}: bei „bestimmte Nummern“ mindestens eine Nummer (mind. 3 Ziffern) angeben")
        days = sorted({int(d) for d in (r.get("days") or []) if str(d).isdigit() and 0 <= int(d) <= 6})
        f, t = (r.get("from") or "").strip(), (r.get("to") or "").strip()
        if bool(f) != bool(t):
            raise ValueError(f"{lab}: Uhrzeit „von“ und „bis“ nur zusammen angeben")
        if f and _min(t, lab) <= _min(f, lab):
            raise ValueError(f"{lab}: „bis“ muss nach „von“ liegen")
        called = [_digits(c) for c in (r.get("called") or []) if _digits(c)]
        target = ""
        if action == "forward":
            try:
                target = dial_number(r.get("target") or "")
            except ValueError as e:
                raise ValueError(f"{lab}: Weiterleitungsziel: {e}")
        out.append({"id": (re.sub(r"\W", "", str(r.get("id") or "")) or secrets.token_hex(4))[:16],
                    "name": (r.get("name") or "").strip()[:60] or f"Regel {i}", "enabled": bool(r.get("enabled", True)),
                    "caller_mode": mode, "callers": callers, "days": days, "from": f, "to": t, "called": called,
                    "action": action, "target": target, "note": (r.get("note") or "").strip()[:120]})
    return out


def same_number(a, b):
    """Gleiche Rufnummer? (eine Stelle fuer db.same_number, Waehlregeln und Fernabfrage)
    Kurze (interne) Nummern exakt (**610 = 610). Sonst national geschrieben (+49/0049 -> 0) und vollstaendig verglichen, damit
    030 12345678 und 040 12345678 verschieden sind; nur wenn eine Seite keine Vorwahl hat (beginnt nicht mit 0), reicht das
    Ende (letzte 9 Ziffern der Seite ohne Vorwahl)."""
    a, b = _nat(a), _nat(b)
    if not a or not b:
        return False
    if min(len(a), len(b)) < 7:
        return a == b
    if a.startswith("0") and b.startswith("0"):
        return a == b
    short, other = (a, b) if not a.startswith("0") and (b.startswith("0") or len(a) <= len(b)) else (b, a)
    return other.endswith(short[-9:])


def _same(caller, pattern):
    """Nummer passt zum Muster: gleiche Nummer (same_number), oder bei 'Muster*' alle Nummern mit diesem Anfang."""
    if str(pattern).endswith("*"):
        a, p = _nat(caller), _nat(pattern)
        return bool(a) and bool(p) and a.startswith(p)
    return same_number(caller, pattern)


def matches(rule, caller, called_digits, now, in_contacts):
    """in_contacts: Funktion nummer -> bool (steht der Anrufer im Telefonbuch?)."""
    if not rule.get("enabled", True):
        return False
    caller = caller or ""
    internal = caller.startswith("**")
    mode = rule["caller_mode"]
    if mode == "list" and not any(_same(caller, c) for c in rule["callers"]):
        return False
    if mode == "contacts" and not in_contacts(caller):
        return False
    if mode == "unknown" and (internal or in_contacts(caller)):
        return False
    if mode == "internal" and not internal:
        return False
    if mode == "external" and internal:
        return False
    if rule["days"] and now.weekday() not in rule["days"]:
        return False
    if rule["from"]:
        mins = now.hour * 60 + now.minute
        # "bis 23:59" = bis Tagesende (wie der Zeitplan, schedule._min), sonst gaelte die Regel in der Minute 23:59 nicht
        if not (_min(rule["from"], "") <= mins < schedule._min(rule["to"])):
            return False
    if rule["called"] and called_digits not in rule["called"]:
        return False
    return True


def first_match(rules, caller, called_digits, now, in_contacts):
    for r in rules or []:
        if matches(r, caller, called_digits, now, in_contacts):
            return r
    return None


def describe(r):
    who = {"list": "Anrufer " + ", ".join(r.get("callers") or []), "any": "alle Anrufer"}.get(r["caller_mode"], MODES[r["caller_mode"]])
    when = ""
    if r["days"] or r["from"]:
        d = "täglich" if not r["days"] or len(r["days"]) == 7 else ", ".join(DAYS[x] for x in r["days"])
        when = f", {d}" + (f" {r['from']}–{r['to']}" if r["from"] else "")
    nums = f", nur Nummer {', '.join(r['called'])}" if r["called"] else ""
    act = ACTIONS[r["action"]] + (f" {r['target']}" if r["action"] == "forward" else "")
    return f"Wenn {who}{when}{nums} → {act}" + (f" ({r['note']})" if r["note"] else "")
