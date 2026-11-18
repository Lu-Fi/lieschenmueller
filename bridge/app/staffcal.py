"""Kalender der Mitarbeiter (Google Kalender): Aufenthalt fuer berechtigte Anrufer, Termine abfragen und planen (Fernabfrage)."""
import os
import time
import zoneinfo
from datetime import datetime, timedelta

from . import db, gcal, remote

DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
WORK_FROM, WORK_TO = 8, 18        # Zeitfenster fuer Terminvorschlaege (Mo-Fr)


def _tz():
    return zoneinfo.ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))


def _dt(ts):
    return datetime.fromtimestamp(ts, _tz())


def day_name(ts):
    d, today = _dt(ts).date(), datetime.now(_tz()).date()
    delta = (d - today).days
    return "heute" if delta == 0 else "morgen" if delta == 1 else f"{DAYS[d.weekday()]}, {d.day}.{d.month}."


def hm(ts):
    return _dt(ts).strftime("%H:%M")


def enabled(st):
    return bool((st.get("cal_id") or "").strip()) and gcal.configured()


def any_enabled():
    return gcal.configured() and bool(db.q("SELECT 1 FROM staff WHERE cal_id!='' LIMIT 1"))


# --- Wer darf erfahren, wo der Mitarbeiter ist? ---
def may_reveal(st, caller):
    """Der Mitarbeiter selbst, andere Mitarbeiter (wenn freigegeben) und die eingetragenen Rufnummern."""
    if not (caller or "").strip():
        return False
    if any(remote._same(caller, n) for n in remote.numbers_of(st)):
        return True
    if st.get("cal_reveal_staff"):
        for o in db.q("SELECT * FROM staff"):
            if any(remote._same(caller, n) for n in remote.numbers_of(o)):
                return True
    return any(remote._same(caller, n) for n in (st.get("cal_reveal") or "").split(",") if n.strip())


def _describe(e, detail):
    if detail == "busy":
        return {"titel": "in einem Termin", "ort": ""}
    return {"titel": e["summary"] or "Termin", "ort": e.get("location", "")}


def whereabouts(st):
    """Wo ist der Mitarbeiter gerade / als Naechstes? Detailgrad laut Einstellung (nur Beschaeftigt-Hinweis oder Titel/Ort)."""
    now = time.time()
    evs = [e for e in gcal.events(st["cal_id"], now - 6 * 3600, now + 36 * 3600) if not e["free"]]
    detail = st.get("cal_detail") or "full"
    cur = next((e for e in evs if e["start"] <= now < e["end"]), None)
    nxt = next((e for e in evs if e["start"] > now), None)
    out = {}
    if cur:
        out["jetzt"] = {**_describe(cur, detail), "bis": f"{day_name(cur['end'])} {hm(cur['end'])} Uhr"}
    else:
        out["jetzt"] = None
    if nxt:
        out["naechster_termin"] = {**_describe(nxt, detail), "beginnt": f"{day_name(nxt['start'])} {hm(nxt['start'])} Uhr"}
    out["hinweis"] = ("Der Mitarbeiter ist laut Kalender gerade in einem Termin." if cur else
                      "Im Kalender steht gerade kein Termin: das heisst nicht, dass er im Buero ist - sage nur, was der Kalender zeigt.")
    return out


# --- Fernabfrage: eigene Termine ---
def my_events(st, days=1):
    """Termine ab jetzt fuer `days` Tage (heute 0:00 bis ...)."""
    now = datetime.now(_tz())
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    t_min, t_max = start.timestamp(), (start + timedelta(days=max(1, min(int(days or 1), 14)))).timestamp()
    evs = [e for e in gcal.events(st["cal_id"], t_min, t_max) if not e["free"]]
    return [{"id": e["id"], "wann": f"{day_name(e['start'])} {hm(e['start'])}–{hm(e['end'])} Uhr", "titel": e["summary"] or "Termin",
             "ort": e.get("location", "")} for e in evs]


def free_times(st, minutes=30, days=3, limit=6):
    """Freie Zeitfenster (Mo-Fr 8-18 Uhr) der naechsten Tage, mindestens `minutes` lang."""
    minutes = max(10, min(int(minutes or 30), 480))
    now = datetime.now(_tz())
    t_min = now.timestamp()
    t_max = (now + timedelta(days=max(1, min(int(days or 3), 14)) + 1)).timestamp()
    busy = sorted((e["start"], e["end"]) for e in gcal.events(st["cal_id"], t_min, t_max) if not e["free"])
    out, day = [], now.replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(max(1, min(int(days or 3), 14)) + 1):
        d = day + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        t = max(d.replace(hour=WORK_FROM).timestamp(), t_min)
        end = d.replace(hour=WORK_TO).timestamp()
        for bs, be in busy + [(end, end)]:
            if be <= t:
                continue
            gap_end = min(bs, end)
            if gap_end - t >= minutes * 60:
                out.append({"von": f"{day_name(t)} {hm(t)} Uhr", "bis": f"{hm(gap_end)} Uhr", "minuten": int((gap_end - t) // 60), "_start": t})
            t = max(t, be)
            if t >= end:
                break
        if len(out) >= limit:
            break
    return [{k: v for k, v in o.items() if k != "_start"} for o in out[:limit]]


def create_event(st, title, start_local, minutes, location=""):
    """Termin eintragen (Beginn als lokale Zeit 'JJJJ-MM-TTTHH:MM'); wirft gcal.Conflict bei Ueberschneidung."""
    start = gcal.parse_local(start_local)
    end = start + max(5, min(int(minutes or 30), 24 * 60)) * 60
    if start < time.time() - 300:
        raise gcal.CalendarError("Der Beginn liegt in der Vergangenheit")
    for e in gcal.events(st["cal_id"], start - 60, end + 60):
        if not e["free"] and e["start"] < end and e["end"] > start:
            raise gcal.Conflict(f"Überschneidung mit „{e['summary'] or 'Termin'}“ ({hm(e['start'])}–{hm(e['end'])} Uhr)")
    gcal.insert(st["cal_id"], (title or "Termin").strip()[:200], "Per Fernabfrage von Lieschen Müller eingetragen.", start, end, (location or "").strip()[:200])
    return f"{day_name(start)} {hm(start)}–{hm(end)} Uhr"
