"""Kampagnen-Logik: Terminpool, Anrufzeiten, Wiederholungen, Ergebnis eines Anrufs, Zeittakt."""
import asyncio
import logging
import time
from datetime import datetime, timedelta

from app import gcal

log = logging.getLogger("plugin.campaigns")

DAYS = {1: "Montag", 2: "Dienstag", 3: "Mittwoch", 4: "Donnerstag", 5: "Freitag", 6: "Samstag", 7: "Sonntag"}
MONTHS = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November",
          "Dezember"]
RUNNING = {}          # target_id -> campaign_id (Anruf laeuft oder wird gewaehlt)
MAX_PARALLEL_TOTAL = 3
api = None            # PluginAPI, von register() gesetzt
SYNC = {}             # pool_id -> Zeitpunkt der letzten Kalender-Abfrage (kurz zwischengespeichert)

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, task TEXT NOT NULL DEFAULT '', agent_id INTEGER,
  status TEXT NOT NULL DEFAULT 'paused', days TEXT NOT NULL DEFAULT '1,2,3,4,5', t_from TEXT NOT NULL DEFAULT '09:00',
  t_to TEXT NOT NULL DEFAULT '17:00', max_attempts INTEGER NOT NULL DEFAULT 3, retry_min INTEGER NOT NULL DEFAULT 120,
  vm_mode TEXT NOT NULL DEFAULT 'retry', vm_text TEXT NOT NULL DEFAULT '', vm_stop INTEGER NOT NULL DEFAULT 0,
  pool_id INTEGER, owner TEXT NOT NULL DEFAULT '', parallel INTEGER NOT NULL DEFAULT 1, created REAL
);
CREATE TABLE IF NOT EXISTS campaign_targets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '', number TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
  last_call_id INTEGER, result TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', updated REAL
);
CREATE TABLE IF NOT EXISTS slot_pools(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS slots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, pool_id INTEGER NOT NULL, start_ts REAL NOT NULL, end_ts REAL NOT NULL,
  label TEXT NOT NULL DEFAULT '', target_id INTEGER, call_id INTEGER, booked_ts REAL
);
CREATE INDEX IF NOT EXISTS idx_slots_pool ON slots(pool_id, start_ts);
CREATE INDEX IF NOT EXISTS idx_targets_camp ON campaign_targets(campaign_id, status, next_at);
"""


COLUMNS = [
    ("slot_pools", "kind", "TEXT NOT NULL DEFAULT 'manual'"), ("slot_pools", "cal_id", "TEXT NOT NULL DEFAULT ''"),
    ("slot_pools", "prefix", "TEXT NOT NULL DEFAULT ''"), ("slot_pools", "slot_min", "INTEGER NOT NULL DEFAULT 30"),
    ("slot_pools", "horizon_days", "INTEGER NOT NULL DEFAULT 60"),
    ("campaigns", "pause_min", "INTEGER NOT NULL DEFAULT 0"), ("campaigns", "wait_note", "TEXT NOT NULL DEFAULT ''"), ("campaigns", "event_title", "TEXT NOT NULL DEFAULT ''"), ("campaigns", "notify_mail", "TEXT NOT NULL DEFAULT ''"),
    ("campaign_targets", "offered", "INTEGER NOT NULL DEFAULT 0"), ("slots", "ext", "TEXT NOT NULL DEFAULT ''"), ("slots", "book_ext", "TEXT NOT NULL DEFAULT ''"),
]


def ensure_columns():
    """Spalten nachtragen, die aeltere Datenbanken noch nicht haben."""
    for table, col, ddl in COLUMNS:
        if not any(r["name"] == col for r in api.db.q(f"PRAGMA table_info({table})")):
            api.db.x(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def q(sql, args=()):
    return api.db.q(sql, args)


def x(sql, args=()):
    return api.db.x(sql, args)


def slot_label(ts):
    d = datetime.fromtimestamp(ts)
    return f"{DAYS[d.isoweekday()]}, {d.day}. {MONTHS[d.month]} um {d.strftime('%H:%M')} Uhr"


ZAHL = {1: "eins", 2: "zwei", 3: "drei", 4: "vier", 5: "fünf", 6: "sechs", 7: "sieben", 8: "acht", 9: "neun", 10: "zehn", 11: "elf", 12: "zwölf"}


def spoken_time(d):
    """Uhrzeit so, wie man sie am Telefon sagt: 'um neun', 'um halb elf', 'um Viertel nach zehn', nachmittags 'um 14 Uhr'."""
    h, m = d.hour, d.minute
    nxt = ZAHL[(h + 1) % 12 or 12]
    if m == 0:
        return f"um {ZAHL[h % 12 or 12]}" if h < 13 else f"um {h} Uhr"
    if m == 30:
        return f"um halb {nxt}"
    if m == 15:
        return f"um Viertel nach {ZAHL[h % 12 or 12]}"
    if m == 45:
        return f"um Viertel vor {nxt}"
    return f"um {h} Uhr {m}"


def spoken_day(d, now=None):
    """'heute', 'morgen', 'übermorgen', Wochentag innerhalb einer Woche, sonst Wochentag mit Datum."""
    today = (now or datetime.now()).date()
    delta = (d.date() - today).days
    if delta == 0:
        return "heute"
    if delta == 1:
        return "morgen"
    if delta == 2:
        return "übermorgen"
    wd = DAYS[d.isoweekday()]
    return wd if delta < 7 else f"{wd}, den {d.day}. {MONTHS[d.month]}"


def spoken_slot(ts):
    d = datetime.fromtimestamp(ts)
    return {"tag": spoken_day(d), "uhrzeit": d.strftime("%H:%M"), "gesprochen": f"{spoken_day(d)} {spoken_time(d)}"}


def _hm(s, default):
    try:
        h, m = (s or default).split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        h, m = default.split(":")
        return int(h) * 60 + int(m)


def in_window(c, now=None):
    now = now or datetime.now()
    days = {int(v) for v in (c["days"] or "").split(",") if v.strip().isdigit()}
    if days and now.isoweekday() not in days:
        return False
    cur = now.hour * 60 + now.minute
    return _hm(c["t_from"], "09:00") <= cur < _hm(c["t_to"], "17:00")


def next_window_start(c, after_ts):
    """Fruehester Zeitpunkt >= after_ts, an dem Anrufe erlaubt sind."""
    t = datetime.fromtimestamp(after_ts)
    for _ in range(8 * 24 * 12):
        if in_window(c, t):
            return t.timestamp()
        t += timedelta(minutes=5)
    return after_ts


# --- Terminpool ---
def pool(pid):
    r = q("SELECT * FROM slot_pools WHERE id=?", (pid,))
    return r[0] if r else None


def pool_pause(pid):
    """Groesste Pause (Minuten) aller Kampagnen, die diesen Pool nutzen."""
    return (q("SELECT COALESCE(MAX(pause_min),0) AS p FROM campaigns WHERE pool_id=?", (pid,)) or [{"p": 0}])[0]["p"] or 0


def free_slots(pool_id, n=3, pause_min=0, limit=None):
    """Freie Termine; Termine, die naeher als die Pause an einem gebuchten Termin liegen, werden nicht angeboten."""
    if not pool_id:
        return []
    p = int(pause_min or 0) * 60
    return q("SELECT s.id, s.start_ts, s.end_ts FROM slots s WHERE s.pool_id=? AND s.target_id IS NULL AND s.start_ts>? "
             "AND NOT EXISTS (SELECT 1 FROM slots b WHERE b.pool_id=s.pool_id AND b.target_id IS NOT NULL "
             "AND b.start_ts < s.end_ts+? AND b.end_ts > s.start_ts-?) ORDER BY s.start_ts LIMIT ?",
             (pool_id, time.time() + 1800, p, p, limit or max(1, min(int(n or 3), 6))))


def book(pool_id, slot_id, target_id, call_id, pause_min=0):
    """Atomar buchen (auch gegen Termine in der Pause). Gibt die Terminbeschreibung zurueck oder None, wenn nicht mehr moeglich."""
    p = int(pause_min or 0) * 60
    n = api.db.x_count("UPDATE slots SET target_id=?, call_id=?, booked_ts=? WHERE id=? AND pool_id=? AND target_id IS NULL "
                       "AND NOT EXISTS (SELECT 1 FROM slots b WHERE b.pool_id=? AND b.target_id IS NOT NULL "
                       "AND b.start_ts < slots.end_ts+? AND b.end_ts > slots.start_ts-?)",
                       (target_id, call_id, time.time(), slot_id, pool_id, pool_id, p, p))
    if not n:
        return None
    return slot_label(q("SELECT start_ts FROM slots WHERE id=?", (slot_id,))[0]["start_ts"])


def release_slot(slot_id, drop=False):
    """Termin freigeben (Kalendereintrag wird geloescht); drop=True entfernt den Slot ganz (z. B. im Kalender nicht mehr frei)."""
    s = (q("SELECT s.*, p.cal_id FROM slots s JOIN slot_pools p ON p.id=s.pool_id WHERE s.id=?", (slot_id,)) or [None])[0]
    if not s:
        return
    if s["book_ext"] and s["cal_id"] and gcal.configured():
        gcal.delete(s["cal_id"], s["book_ext"])
    if drop:
        x("DELETE FROM slots WHERE id=?", (slot_id,))
    else:
        x("UPDATE slots SET target_id=NULL, call_id=NULL, booked_ts=NULL, book_ext='' WHERE id=?", (slot_id,))
    SYNC.pop(s["pool_id"], None)      # Pool beim naechsten Zugriff sofort neu abgleichen


def release_appointment(target_id):
    """Gebuchten Termin eines Ziels freigeben (Kalendereintrag wird geloescht). Das Ziel bekommt keinen neuen Anruf (Status 'erledigt'),
    ausser man setzt es mit 'Neu' zurueck. Gibt die Zahl der freigegebenen Termine zurueck."""
    n = len(q("SELECT id FROM slots WHERE target_id=?", (target_id,)))
    release(target_id)
    if n:
        x("UPDATE campaign_targets SET status='done', result='Termin freigegeben', updated=? WHERE id=? AND status='booked'", (time.time(), target_id))
    return n


def release(target_id):
    for s in q("SELECT id FROM slots WHERE target_id=?", (target_id,)):
        release_slot(s["id"])


# --- Google Kalender als Terminpool ---
def candidates(p, pause_min, now=None):
    """Aus den Platzhalter-Terminen (Titel beginnt mit dem Praefix des Pools) freie Termine bilden.
    Nicht-Platzhalter-Termine des Kalenders gelten als belegt; zwischen allen Terminen liegt mindestens die Pause."""
    now = now or time.time()
    horizon = now + max(1, int(p["horizon_days"] or 60)) * 86400
    evs = gcal.events(p["cal_id"], now, horizon)
    pre = (p["prefix"] or "").strip().lower()
    if not pre:
        raise gcal.CalendarError("Für den Kalender-Pool ist kein Präfix der Platzhalter-Termine eingetragen")
    windows = [e for e in evs if e["summary"].strip().lower().startswith(pre)]
    busy = [(e["start"], e["end"]) for e in evs if not e["summary"].strip().lower().startswith(pre) and not e["free"]]
    busy += [(b["start_ts"], b["end_ts"]) for b in q("SELECT start_ts, end_ts FROM slots WHERE pool_id=? AND target_id IS NOT NULL", (p["id"],))]
    busy.sort()
    P, D = int(pause_min or 0) * 60, max(5, int(p["slot_min"] or 30)) * 60
    out = []
    for w in sorted(windows, key=lambda e: e["start"]):
        t = max(w["start"], now + 1800)
        while t + D <= w["end"]:
            hit = next(((bs, be) for bs, be in busy if bs - P < t + D and be + P > t), None)
            if hit:
                t = max(t + 60, hit[1] + P)       # hinter den belegten Termin springen
                continue
            out.append({"start": t, "end": t + D, "ext": w["id"]})
            t += D + P
    return out


def sync_pool(p, pause_min=None, force=False, max_age=60):
    """Kalender-Pool abgleichen: freie Termine in `slots` ablegen, nicht mehr freie entfernen. Gebuchte bleiben unberuehrt."""
    if p["kind"] != "gcal":
        return {"added": 0, "removed": 0}
    if not force and time.time() - SYNC.get(p["id"], 0) < max_age:
        return {"added": 0, "removed": 0, "cached": True}
    pause = pool_pause(p["id"]) if pause_min is None else pause_min
    cands = candidates(p, pause)
    reconcile_booked(p)
    have = {s["start_ts"]: s for s in q("SELECT * FROM slots WHERE pool_id=? AND target_id IS NULL", (p["id"],))}
    want = {c["start"]: c for c in cands}
    removed = added = 0
    for ts, s in have.items():
        if ts not in want or abs(s["end_ts"] - want[ts]["end"]) > 1:
            x("DELETE FROM slots WHERE id=?", (s["id"],))
            removed += 1
    for ts, c in want.items():
        if ts not in have or abs(have[ts]["end_ts"] - c["end"]) > 1:
            x("INSERT INTO slots(pool_id,start_ts,end_ts,ext) VALUES(?,?,?,?)", (p["id"], c["start"], c["end"], c["ext"]))
            added += 1
    SYNC[p["id"]] = time.time()
    return {"added": added, "removed": removed}


def reconcile_booked(p):
    """Im Kalender geloeschte Buchungstermine erkennen: Slot wird frei, das Ziel gilt nicht mehr als gebucht."""
    booked = q("SELECT * FROM slots WHERE pool_id=? AND target_id IS NOT NULL AND book_ext!='' AND start_ts>? AND booked_ts<?",
               (p["id"], time.time(), time.time() - 30))
    if not booked:
        return
    ids = {e["id"] for e in gcal.events(p["cal_id"], min(b["start_ts"] for b in booked) - 60, max(b["end_ts"] for b in booked) + 60)}
    for b in booked:
        if b["book_ext"] not in ids:
            x("UPDATE slots SET target_id=NULL, call_id=NULL, booked_ts=NULL, book_ext='' WHERE id=?", (b["id"],))
            x("UPDATE campaign_targets SET status='done', result='Termin im Kalender gelöscht', updated=? WHERE id=? AND status='booked'",
              (time.time(), b["target_id"]))
            api.publish({"type": "campaign"})


def book_in_calendar(p, camp, target, slot_id, call_id):
    """Termin im Google Kalender eintragen; vorher kurz pruefen, ob er dort noch frei ist. Wirft Conflict/CalendarError."""
    s = q("SELECT * FROM slots WHERE id=?", (slot_id,))[0]
    P = int(camp.get("pause_min") or 0) * 60
    pre = (p["prefix"] or "").strip().lower()
    for e in gcal.events(p["cal_id"], s["start_ts"] - P - 60, s["end_ts"] + P + 60):
        if e["summary"].strip().lower().startswith(pre) or e["free"]:
            continue
        if e["start"] < s["end_ts"] + P and e["end"] > s["start_ts"] - P:
            raise gcal.Conflict("Der Termin ist im Kalender inzwischen belegt")
    title = (camp.get("event_title") or "Termin: {name}").replace("{name}", target.get("name") or target.get("number") or "").replace("{kampagne}", camp["name"])
    desc = f"Von der KI-Kampagne „{camp['name']}“ gebucht.\nTelefon: {target.get('number', '')}" + (f"\nAnruf-ID: {call_id}" if call_id else "")
    ext = gcal.insert(p["cal_id"], title, desc, s["start_ts"], s["end_ts"])
    x("UPDATE slots SET book_ext=? WHERE id=?", (ext, slot_id))
    return ext


# --- Ergebnis eines Anrufs ---
def _retry(camp, t, why):
    t = q("SELECT * FROM campaign_targets WHERE id=?", (t["id"],))[0]
    if t["attempts"] >= camp["max_attempts"]:
        x("UPDATE campaign_targets SET status='exhausted', result=?, updated=? WHERE id=?",
          (f"Aufgegeben nach {t['attempts']} Versuchen ({why})", time.time(), t["id"]))
    else:
        nxt = next_window_start(camp, time.time() + camp["retry_min"] * 60)
        x("UPDATE campaign_targets SET status='pending', next_at=?, result=?, updated=? WHERE id=?",
          (nxt, f"Nächster Versuch: {why}", time.time(), t["id"]))


def finish(ctx, flags):
    """Nach dem Gespraech: Ziel abschliessen oder neuen Versuch einplanen.
    ctx: {"campaign_id", "target_id"}; flags: booked, machine, vm_left, result ({ergebnis, zusammenfassung, erledigt}), spoke."""
    tid, cid = ctx.get("target_id"), ctx.get("campaign_id")
    RUNNING.pop(tid, None)
    camp = (q("SELECT * FROM campaigns WHERE id=?", (cid,)) or [None])[0]
    t = (q("SELECT * FROM campaign_targets WHERE id=?", (tid,)) or [None])[0]
    if not camp or not t:
        return
    now = time.time()
    res = flags.get("result") or {}
    summ = res.get("zusammenfassung", "")
    if flags.get("booked"):
        x("UPDATE campaign_targets SET status='booked', result=?, summary=?, updated=? WHERE id=?",
          (f"Termin: {flags['booked']}", summ, now, tid))
    elif flags.get("machine"):
        if flags.get("vm_left") and camp["vm_stop"]:
            x("UPDATE campaign_targets SET status='voicemail', result='Nachricht auf Anrufbeantworter hinterlassen', updated=? WHERE id=?",
              (now, tid))
        else:
            _retry(camp, t, "Anrufbeantworter" + (" (Nachricht hinterlassen)" if flags.get("vm_left") else ""))
    elif res and res.get("erledigt"):
        x("UPDATE campaign_targets SET status='done', result=?, summary=?, updated=? WHERE id=?",
          (res.get("ergebnis", "erledigt")[:200], summ, now, tid))
    elif res:
        _retry(camp, t, "Rückruf gewünscht: " + res.get("ergebnis", "")[:100])
        x("UPDATE campaign_targets SET summary=? WHERE id=?", (summ, tid))
    elif flags.get("spoke"):
        _retry(camp, t, "Gespräch ohne Ergebnis")
    else:
        _retry(camp, t, "nicht erreicht")
    refresh_status(cid)
    api.publish({"type": "campaign", "campaign_id": cid})


def refresh_status(cid):
    """Kampagne ist fertig, wenn kein Ziel mehr offen ist."""
    open_n = q("SELECT COUNT(*) n FROM campaign_targets WHERE campaign_id=? AND status IN ('pending','calling')", (cid,))[0]["n"]
    if not open_n:
        x("UPDATE campaigns SET status='done' WHERE id=? AND status='active'", (cid,))


def pool_info(camp, refresh=True):
    """Kennzahlen zum Terminpool einer Kampagne: freie Termine und wem schon Termine angeboten wurden."""
    cid = camp["id"]
    out = {"pool_free": 0, "pool_total": 0, "pool_error": "", "open_n": 0, "offered_n": 0, "notoffered_open": 0, "notoffered_total": 0}
    if not camp.get("pool_id"):
        return out
    p = pool(camp["pool_id"])
    if p and p["kind"] == "gcal" and refresh:
        try:
            sync_pool(p)
        except gcal.CalendarError as e:
            out["pool_error"] = str(e)[:160]
    out["pool_free"] = len(free_slots(camp["pool_id"], 1000, camp.get("pause_min") or 0))
    out["pool_total"] = (q("SELECT COUNT(*) n FROM slots WHERE pool_id=? AND start_ts>?", (camp["pool_id"], time.time())) or [{"n": 0}])[0]["n"]
    r = q("SELECT "
          "SUM(CASE WHEN status IN ('pending','calling') THEN 1 ELSE 0 END) AS open_n, "
          "SUM(CASE WHEN offered=1 THEN 1 ELSE 0 END) AS offered_n, "
          "SUM(CASE WHEN offered=0 AND status IN ('pending','calling') THEN 1 ELSE 0 END) AS notoffered_open, "
          "SUM(CASE WHEN offered=0 AND status IN ('pending','calling','exhausted','failed','voicemail') THEN 1 ELSE 0 END) AS notoffered_total "
          "FROM campaign_targets WHERE campaign_id=?", (cid,))[0]
    out.update({k: (r[k] or 0) for k in ("open_n", "offered_n", "notoffered_open", "notoffered_total")})
    return out


def stats(cid):
    out = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM campaign_targets WHERE campaign_id=? GROUP BY status", (cid,))}
    out["total"] = sum(out.values())
    return out


def recover():
    """Nach einem Neustart: begonnene Anrufe sind verloren, die Ziele kommen wieder an die Reihe."""
    x("UPDATE campaign_targets SET status='pending' WHERE status='calling'")


async def launch(camp, target):
    """Startet den Anruf an ein Ziel. Gibt die Anruf-ID zurueck, -1 bei ungueltiger Nummer, None bei Fehlern."""
    try:
        number = api.dial_number(target["number"])
    except ValueError as e:
        x("UPDATE campaign_targets SET status='failed', result=?, updated=? WHERE id=?", (f"Ungueltige Nummer: {e}", time.time(), target["id"]))
        RUNNING.pop(target["id"], None)
        return -1
    agent = next((a for a in api.db.list_agents(enabled_only=True) if a["id"] == camp["agent_id"] and a["mode"] == "assist"), None) \
        or next((a for a in api.db.list_agents(enabled_only=True) if a["mode"] == "assist"), None)
    if not agent:
        return None
    task = camp["task"].replace("{name}", target["name"] or "")
    return await api.start_outbound(number, target["name"] or "", task, agent, camp.get("owner") or "",
                                    extra={"plg_campaigns": {"campaign": camp, "target": target, "campaign_id": camp["id"],
                                                             "target_id": target["id"]},
                                           "no_vm_hint": True},
                                    label=f"Kampagne: {camp['name']}")


def pool_block(camp):
    """Grund, warum jetzt nicht angerufen werden soll (leer = alles in Ordnung): Termin-Kampagne ohne freien Termin oder Kalender nicht erreichbar."""
    if not camp.get("pool_id"):
        return ""
    p = pool(camp["pool_id"])
    if not p:
        return ""
    if p["kind"] == "gcal":
        try:
            sync_pool(p, None, max_age=120 if camp.get("wait_note") else 30)
        except gcal.CalendarError as e:
            return f"Kalender nicht erreichbar ({str(e)[:80]})"
    if not free_slots(camp["pool_id"], 1, camp.get("pause_min") or 0):
        return "keine freien Termine"
    return ""


def reap():
    """Verwaiste Ziele ('wird angerufen', aber der Anruf ist laengst zu Ende, z. B. weil das Plugin zwischendurch ausgeschaltet war) neu einplanen."""
    now = time.time()
    for t in q("SELECT * FROM campaign_targets WHERE status='calling'"):
        row = (q("SELECT status, ended FROM calls WHERE id=?", (t["last_call_id"],)) or [None])[0] if t["last_call_id"] else None
        running = row is not None and row["status"] in ("aktiv", "waehlt") and not row["ended"]
        if running or now - (t["updated"] or 0) < 180:
            continue
        camp = (q("SELECT * FROM campaigns WHERE id=?", (t["campaign_id"],)) or [None])[0]
        RUNNING.pop(t["id"], None)
        if camp:
            _retry(camp, t, "Anruf ohne Rückmeldung beendet")
            refresh_status(camp["id"])
    for tid in [i for i in RUNNING if not q("SELECT 1 FROM campaign_targets WHERE id=? AND status='calling'", (i,))]:
        RUNNING.pop(tid, None)


async def tick():
    """Faellige Ziele anrufen, solange das Zeitfenster der Kampagne offen ist (alle 20 s aufgerufen)."""
    await asyncio.to_thread(reap)
    for camp in q("SELECT * FROM campaigns WHERE status='active'"):
        if not in_window(camp):
            continue
        running = [t for t, c in RUNNING.items() if c == camp["id"]]
        if len(running) >= max(1, camp["parallel"]) or len(RUNNING) >= MAX_PARALLEL_TOTAL:
            continue
        t = (q("SELECT * FROM campaign_targets WHERE campaign_id=? AND status='pending' AND next_at<=? ORDER BY next_at, id LIMIT 1",
               (camp["id"], time.time())) or [None])[0]
        if not t:
            refresh_status(camp["id"])
            continue
        why = await asyncio.to_thread(pool_block, camp)      # nicht anrufen, wenn nichts angeboten werden kann
        if why != (camp.get("wait_note") or ""):
            x("UPDATE campaigns SET wait_note=? WHERE id=?", (why, camp["id"]))
            api.publish({"type": "campaign", "campaign_id": camp["id"]})
        if why:
            continue
        x("UPDATE campaign_targets SET status='calling', attempts=attempts+1, updated=? WHERE id=?", (time.time(), t["id"]))
        RUNNING[t["id"]] = camp["id"]
        try:
            cid = await launch(camp, dict(t, attempts=t["attempts"] + 1))
        except Exception:
            log.exception("Kampagnenanruf konnte nicht gestartet werden")
            cid = None
        if cid is None:
            RUNNING.pop(t["id"], None)
            _retry(camp, t, "Start fehlgeschlagen")
        else:
            x("UPDATE campaign_targets SET last_call_id=? WHERE id=?", (cid, t["id"]))
