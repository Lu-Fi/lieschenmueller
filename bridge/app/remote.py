"""Fernabfrage: Mitarbeiter fragen ihre Anrufe per Telefon ab (erkannt an der Rufnummer oder an Codewoertern)."""
import json
import re
import time

from . import db


def _same(a, b):
    """Rufnummern gleich? (db.same_number: interne exakt, sonst mit Vorwahl vollstaendig)"""
    return db.same_number(a, b)


def numbers_of(st):
    """Rufnummern, von denen aus die Fernabfrage greift: eigene, Nummer ausser Haus (Handy) und hinterlegte."""
    return [n for n in [st.get("number"), st.get("away_number"), *(st.get("remote_numbers") or "").split(",")] if n and n.strip()]


def staff_by_caller(number):
    if not db._digits(number):
        return None
    for st in db.q("SELECT * FROM staff WHERE remote_on=1 AND remote_codes!=''"):      # Codewoerter sind Pflicht
        if any(_same(number, n) for n in numbers_of(st)):
            return None if locked(st) else st      # gesperrt: Anruf laeuft wie jeder andere (keine Fernabfrage moeglich)
    return None


# Sperre nach Fehlversuchen: die Rufnummer laesst sich faelschen, die Codewoerter sind dann die einzige Huerde
FAIL_MAX = 3            # so viele Anrufe ohne Freischaltung ...
FAIL_WINDOW_S = 3600    # ... innerhalb einer Stunde
LOCK_S = 1800           # sperren die Fernabfrage fuer 30 Minuten


def _fails(st):
    try:
        ts = json.loads(db.get_meta(f"remote_fail:{st['id']}", "[]"))
    except ValueError:
        ts = []
    return [t for t in ts if isinstance(t, (int, float)) and time.time() - t < FAIL_WINDOW_S]


def locked(st):
    ts = _fails(st)
    return len(ts) >= FAIL_MAX and time.time() - max(ts) < LOCK_S


def note_fail(st):
    """Anruf von einer Fernabfrage-Nummer endete ohne Freischaltung. Gibt True zurueck, wenn die Fernabfrage jetzt gesperrt ist."""
    ts = _fails(st) + [time.time()]
    db.set_meta(f"remote_fail:{st['id']}", json.dumps(ts[-FAIL_MAX:]))
    return locked(st)


def note_ok(st):
    db.set_meta(f"remote_fail:{st['id']}", "[]")


def _norm(text):
    """Kleinbuchstaben, Satzzeichen weg, einfache Leerzeichen (fuer wortgenauen Vergleich)."""
    return " ".join(re.sub(r"[^\w]+", " ", (text or "").lower()).split())


def codes_of(st):
    return [c for c in (_norm(c) for c in (st.get("remote_codes") or "").split(",")) if c]


def check_codes(text, seen, st):
    """Codewoerter im Anruferdialog: gilt nur fuer den Mitarbeiter, dessen Rufnummer erkannt wurde (st); alle Woerter muessen fallen,
    jedes als ganzes Wort ("Rose" schaltet nicht mit "Rosenkohl" frei). text: bisher Gesagtes des Anrufers (Bruchstuecke der
    Transkription zusammengesetzt), seen: je Anruf, Menge der gehoerten Codewoerter."""
    codes = codes_of(st)
    if not codes:
        return False
    t = f" {_norm(text)} "
    seen.update(c for c in codes if f" {c} " in t)
    return len(seen) >= len(codes)


def _when(ts):
    import datetime as dt
    d, now = dt.datetime.fromtimestamp(ts), dt.datetime.now()
    day = "heute" if d.date() == now.date() else "gestern" if (now.date() - d.date()).days == 1 else d.strftime("am %d.%m.")
    return f"{day} um {d.strftime('%H:%M')} Uhr"


def _info(row):
    import json
    try:
        meta = json.loads(row.get("meta") or "{}")
    except ValueError:
        meta = {}
    c = db.find_contact(row["caller"])
    return {"id": row["id"], "wann": _when(row["started"]), "von": c["name"] if c else (row["caller"] or "unbekannt"),
            "nummer_angerufen": row["called"], "status": row["status"], "thema": meta.get("anliegen", ""),
            "dringlichkeit": meta.get("dringlichkeit", ""), "mit_aufnahme": bool(row["recording"]),
            "zusammenfassung_vorhanden": bool(meta.get("zusammenfassung"))}


def list_calls(st, zeitraum="neue", anzahl=8):
    """Eingehende Anrufe auf die Nummern des Mitarbeiters. 'neue' = seit der letzten Fernabfrage."""
    nums = db.msn_numbers(st["id"])
    if not nums:
        return {"anrufe": [], "hinweis": "Diesem Mitarbeiter ist keine Rufnummer zugewiesen."}, []
    import datetime as dt
    now = dt.datetime.now()
    midnight = dt.datetime(now.year, now.month, now.day).timestamp()
    fresh = db.q("SELECT remote_last FROM staff WHERE id=?", (st["id"],))
    last = fresh[0]["remote_last"] if fresh else (st.get("remote_last") or 0)
    since = {"heute": midnight, "gestern": midnight - 86400, "7tage": time.time() - 7 * 86400}.get(zeitraum, last or 0)
    until = midnight if zeitraum == "gestern" else time.time() + 1
    own_sql = " OR ".join("(digits(called)=? AND started>=?)" for _ in nums)     # je Nummer erst ab ihrer Zuweisung
    rows = db.q(f"SELECT * FROM calls WHERE direction='in' AND ({own_sql}) AND started>? AND started<? "
                f"AND status NOT IN ('abgebrochen') ORDER BY started {'ASC' if zeitraum == 'neue' else 'DESC'} LIMIT 120",
                [*(v for n in nums for v in n), since, until])
    # Anrufe von den eigenen Nummern des Mitarbeiters (u. a. die Fernabfragen selbst) sind keine Anrufe, die zu melden waeren
    own = numbers_of(st)
    rows = [r for r in rows if not any(_same(r["caller"], n) for n in own)][:40]
    n = max(1, min(int(anzahl or 8), 10))
    shown = rows[:n]
    return {"gesamt": len(rows), "genannt": len(shown), "anrufe": [_info(r) for r in shown],
            "zeitraum": zeitraum, "mehr_vorhanden": len(rows) > n,
            "reihenfolge": "aelteste zuerst; die uebrigen neuen Anrufe kommen bei der naechsten Abfrage" if zeitraum == "neue" else "neueste zuerst"}, shown


def get_call(st, call_id):
    """Anruf nur herausgeben, wenn er auf eine Nummer des Mitarbeiters ging."""
    try:
        cid = int(call_id)
    except (TypeError, ValueError):
        return None
    r = db.q("SELECT * FROM calls WHERE id=?", (cid,))
    d = db._digits(r[0]["called"]) if r else ""
    if not r or not any(d == n and (r[0]["started"] or 0) >= since for n, since in db.msn_numbers(st["id"])):
        return None
    return r[0]


def transcript(cid, limit=3500):
    lab = {"caller": "Anrufer", "agent": "Assistent", "local": "Mitarbeiter", "remote": "Gegenseite", "mix": "Gespräch"}
    rows = db.q("SELECT speaker, text FROM segments WHERE call_id=? AND speaker IN ('caller','agent','local','remote','mix') ORDER BY id", (cid,))
    return "\n".join(f"{lab.get(r['speaker'], r['speaker'])}: {r['text']}" for r in rows)[:limit]


def staff_internal(number):
    """Mitarbeiter, dessen interner Anschluss (z. B. **610) anruft. Interne Kennungen kommen von der Fritzbox und lassen sich von aussen nicht faelschen."""
    n = (number or "").strip()
    if not n.startswith("**"):
        return None
    for st in db.q("SELECT * FROM staff WHERE number!=''"):
        if _same(n, st["number"]):
            return st
    return None
