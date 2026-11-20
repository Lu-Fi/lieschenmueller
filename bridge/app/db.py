import json
import os
import re
import sqlite3
import threading
import time

from . import dialrules as drules
from . import labels
from . import schedule as sched

DB_PATH = os.environ.get("DB_PATH", "/data/lieschen.db")
_lock = threading.Lock()
_conn = None

OLD_NOTICES = ("Hinweis: Sie sprechen mit einer KI. Das Gespräch wird transkribiert.",
               "Hinweis: Sie sprechen mit einem KI-Assistenten. Das Gespräch wird zur Bearbeitung "
               "transkribiert und an Google übertragen.",
               "Hinweis: Sie sprechen mit einer KI. Das Gespräch wird transkribiert und an Google übertragen.")
DEFAULT_NOTICE = "Sie sprechen mit einer KI. Das Gespräch wird transkribiert."
AGENT_FIELDS = ["name", "enabled", "priority", "match_called", "match_caller",
                "keywords", "prompt", "voice", "mode", "greeting",
                "silence_ask", "silence_hangup", "transfer_to", "questions", "tools", "record", "record_ai", "transcribe", "stop_cmd", "rec_always", "model", "thinking_level", "notice", "notice_on", "use_base", "dial_callers", "schedule", "answer_delay", "owner_staff_id", "keyword_count", "keyword_only", "mood_on", "mood_text", "mood_trigger", "mood_min", "trigger_mode", "meta_on", "allow_connect", "ask_before", "limit_calls_h", "limit_min_day", "max_call_s", "speech_pace", "speech_style", "meta_detail", "ring_fallback"]
INT_FIELDS = {"ring_fallback", "ask_before", "allow_connect", "mood_min", "meta_on", "mood_on", "mood_text", "keyword_only", "keyword_count", "enabled", "priority", "meta_detail", "silence_ask", "silence_hangup", "record", "record_ai", "transcribe", "stop_cmd", "rec_always", "notice_on", "use_base", "answer_delay", "owner_staff_id", "limit_calls_h", "limit_min_day", "max_call_s"}
DEFAULTS = {"ask_before": 0, "allow_connect": 1, "mood_min": 4, "trigger_mode": "any", "keyword_count": 1, "use_base": 1, "notice_on": 1, "thinking_level": "low", "enabled": 1, "priority": 100, "silence_ask": 12, "silence_hangup": 10, "tools": "[]", "record": 0, "record_ai": 0, "transcribe": 0, "mood_text": 0, "stop_cmd": 1, "rec_always": 0}

MIGRATIONS = [
    ("staff", "cal_id", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "cal_detail", "TEXT NOT NULL DEFAULT 'full'"),
    ("staff", "cal_reveal", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "cal_reveal_staff", "INTEGER NOT NULL DEFAULT 0"),
    ("calls", "notified", "INTEGER NOT NULL DEFAULT 0"),
    ("staff", "remote_on", "INTEGER NOT NULL DEFAULT 0"),
    ("staff", "remote_numbers", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "remote_codes", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "remote_last", "REAL NOT NULL DEFAULT 0"),
    ("agents", "mood_trigger", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "mood_min", "INTEGER NOT NULL DEFAULT 4"),
    ("agents", "trigger_mode", "TEXT NOT NULL DEFAULT 'any'"),
    ("agents", "meta_on", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "meta_detail", "INTEGER NOT NULL DEFAULT 0"),            # ausfuehrliche Zusammenfassung: alle besprochenen Punkte (optional)
    ("calls", "meta", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "mood_on", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "mood_agent", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "keyword_only", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "keyword_count", "INTEGER NOT NULL DEFAULT 1"),
    ("agents", "silence_ask", "INTEGER NOT NULL DEFAULT 12"),
    ("agents", "silence_hangup", "INTEGER NOT NULL DEFAULT 10"),
    ("agents", "transfer_to", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "questions", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "tools", "TEXT NOT NULL DEFAULT '[]'"),
    ("agents", "record", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "mood_text", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "rec_always", "INTEGER NOT NULL DEFAULT 0"), ("agents", "stop_cmd", "INTEGER NOT NULL DEFAULT 1"), ("calls", "rec_voicestop", "INTEGER NOT NULL DEFAULT 1"), ("calls", "rec_stopped", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "record_ai", "INTEGER NOT NULL DEFAULT 0"), ("agents", "transcribe", "INTEGER NOT NULL DEFAULT 0"), ("calls", "rec_keep", "INTEGER NOT NULL DEFAULT -1"), ("calls", "rec_transcribe", "INTEGER NOT NULL DEFAULT 1"),
    ("calls", "notes", "TEXT NOT NULL DEFAULT '{}'"),
    ("calls", "recording", "TEXT NOT NULL DEFAULT ''"),
    ("calls", "archived", "INTEGER NOT NULL DEFAULT 0"),
    ("calls", "memo", "TEXT NOT NULL DEFAULT ''"),
    ("mail_recipients", "inc_details", "INTEGER NOT NULL DEFAULT 1"), ("mail_recipients", "inc_transcript", "INTEGER NOT NULL DEFAULT 0"),
    ("mail_recipients", "inc_audio", "INTEGER NOT NULL DEFAULT 0"), ("mail_recipients", "inc_points", "INTEGER NOT NULL DEFAULT 0"), ("mail_recipients", "on_rec", "INTEGER NOT NULL DEFAULT 0"), ("mail_recipients", "on_status", "TEXT NOT NULL DEFAULT ''"),
    ("mail_recipients", "on_anomaly", "INTEGER NOT NULL DEFAULT 0"),      # Stoerungs-/Auffaelligkeitsmeldungen (anomaly.py), unabhaengig von Anruf-Mails
    ("calls", "agent_chain", "TEXT NOT NULL DEFAULT ''"),
    ("calls", "direction", "TEXT NOT NULL DEFAULT 'in'"),
    ("calls", "task", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "msn", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "record", "INTEGER NOT NULL DEFAULT 0"),
    ("staff", "deputy_id", "INTEGER"),
    ("staff", "until", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "model", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "notice", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "use_base", "INTEGER NOT NULL DEFAULT 1"),
    ("agents", "owner_staff_id", "INTEGER NOT NULL DEFAULT 0"),
    ("calls", "owner_staff_id", "INTEGER"),
    ("agents", "schedule", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "answer_delay", "INTEGER NOT NULL DEFAULT 0"),
    ("staff", "hours", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "schedule", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "dialrules", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "ai_fallback", "INTEGER NOT NULL DEFAULT 1"),
    # Anreden der internen Rueckfrage-KI (kommagetrennt, z. B. "Petra, Biene, Schatzi"); die Bridge waehlt je Anruf eine aus
    ("staff", "nicknames", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "dnd_allow", "TEXT NOT NULL DEFAULT ''"),
    # ausgehende Gespraeche ohne Vorwahl mitschreiben (outrec.py): off | record | transcript | numbers (nur an rec_out_numbers)
    ("staff", "rec_out", "TEXT NOT NULL DEFAULT 'off'"),
    ("staff", "rec_out_numbers", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "rec_out_notice", "INTEGER NOT NULL DEFAULT 1"),        # Hinweis-Ansage an den Gespraechspartner
    # sieht zusaetzlich die Anrufe auf die Rufnummern dieser Mitarbeiter/Sammelziele (kommagetrennte IDs, z. B. die Zentrale)
    ("staff", "see_staff", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "presence_on", "INTEGER NOT NULL DEFAULT 0"),
    ("staff", "presence_mac", "TEXT NOT NULL DEFAULT ''"),
    ("staff", "presence_seen", "REAL NOT NULL DEFAULT 0"),
    ("staff", "use_hours", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "dial_callers", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "notice_on", "INTEGER NOT NULL DEFAULT 1"),
    ("agents", "allow_connect", "INTEGER NOT NULL DEFAULT 1"),
    ("agents", "ask_before", "INTEGER NOT NULL DEFAULT 0"),
    ("calls", "user_id", "INTEGER NOT NULL DEFAULT 0"),
    ("calls", "rec_mail", "TEXT NOT NULL DEFAULT ''"),
    ("rec_rules", "mail", "TEXT NOT NULL DEFAULT ''"),
    ("rec_rules", "direction", "TEXT NOT NULL DEFAULT 'in'"),
    ("rec_rules", "notice", "INTEGER NOT NULL DEFAULT 1"),
    ("rec_rules", "rec_screen", "INTEGER NOT NULL DEFAULT 0"),      # Rueckfrage der KI bei einem Benutzer mit aufnehmen (optional)
    ("agents", "thinking_level", "TEXT NOT NULL DEFAULT 'low'"),
    ("segments", "agent", "TEXT NOT NULL DEFAULT ''"),
    ("contacts", "source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("calls", "tok_in_text", "INTEGER NOT NULL DEFAULT 0"),
    ("calls", "tok_in_audio", "INTEGER NOT NULL DEFAULT 0"),
    ("calls", "tok_out_text", "INTEGER NOT NULL DEFAULT 0"),
    ("calls", "tok_out_audio", "INTEGER NOT NULL DEFAULT 0"),
    ("calls", "tok_think", "INTEGER NOT NULL DEFAULT 0"),
    ("calls", "cost_usd", "REAL NOT NULL DEFAULT 0"),
    # zuletzt von einem Administrator gesetzte "Nummer im Buero": nur diese (oder eine interne **NNN) gilt fuer
    # eingeschraenkte Konten als eigenes Geraet; Altbestand gilt als vom Administrator gesetzt (Migration unten)
    ("staff", "number_admin", "TEXT NOT NULL DEFAULT ''"),
    # Kostenbremse je Agent (0 = Standard aus den Einstellungen, dort 0 = aus) und Hoechstdauer je Agent (0 = MAX_CALL_SECONDS)
    ("agents", "limit_calls_h", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "limit_min_day", "INTEGER NOT NULL DEFAULT 0"),
    ("agents", "max_call_s", "INTEGER NOT NULL DEFAULT 0"),
    # Sprechtempo (leer = normal, slow, fast) und freier Sprechstil je Agent
    ("agents", "speech_pace", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "speech_style", "TEXT NOT NULL DEFAULT ''"),
    ("agents", "ring_fallback", "INTEGER NOT NULL DEFAULT 0"),
    # Anrufregel "mehrfach angerufen": mindestens repeat_n fruehere Anrufe derselben Nummer in den letzten repeat_min Minuten (0 = egal)
    ("call_rules", "repeat_n", "INTEGER NOT NULL DEFAULT 0"),
    ("call_rules", "repeat_min", "INTEGER NOT NULL DEFAULT 0"),
    # welche frueheren Anrufe zaehlen: '' alle, 'hangup' nur aufgelegte (niemanden erreicht), 'nomsg' alle ohne Nachricht
    ("call_rules", "repeat_kind", "TEXT NOT NULL DEFAULT ''"),
    # "Angemeldet bleiben": laengere Laufzeit der Sitzung (users.SESSION_IDLE_REMEMBER / SESSION_MAX_REMEMBER)
    ("sessions", "remember", "INTEGER NOT NULL DEFAULT 0"),
    # Anrede eines Kontakts ('' unbekannt, 'Herr', 'Frau'); auch bei Fritzbox-Kontakten setzbar, der Abgleich laesst sie stehen
    ("contacts", "salutation", "TEXT NOT NULL DEFAULT ''"),
    # Herkunft des Status: manual (von Hand), presence (WLAN-Anwesenheit), until ("abwesend bis" abgelaufen); Altbestand = von Hand
    ("staff", "status_src", "TEXT NOT NULL DEFAULT 'manual'"),
    # Art des Eintrags: person oder group (Sammelziel wie "Zentrale": nur Nummern, kein Status/Anwesenheit/Konto); Altbestand = Person
    ("staff", "kind", "TEXT NOT NULL DEFAULT 'person'"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL DEFAULT 100,
  match_called TEXT NOT NULL DEFAULT '',
  match_caller TEXT NOT NULL DEFAULT '',
  keywords TEXT NOT NULL DEFAULT '',
  prompt TEXT NOT NULL DEFAULT '',
  voice TEXT NOT NULL DEFAULT 'Kore',
  mode TEXT NOT NULL DEFAULT 'assist',
  greeting TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uuid TEXT UNIQUE,
  started REAL, ended REAL,
  caller TEXT, called TEXT,
  agent_name TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, number TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS templates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, task TEXT NOT NULL, agent_id INTEGER
);
CREATE TABLE IF NOT EXISTS staff(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
  number TEXT NOT NULL DEFAULT '', away_number TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'office', note TEXT NOT NULL DEFAULT '',
  deputy_id INTEGER, until TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE, pw_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user', staff_id INTEGER, disabled INTEGER NOT NULL DEFAULT 0,
  created REAL, last_login REAL
);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created REAL, expires REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS call_rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, priority INTEGER NOT NULL DEFAULT 100,
  match_called TEXT NOT NULL DEFAULT '', match_caller TEXT NOT NULL DEFAULT '', schedule TEXT NOT NULL DEFAULT '',
  agent_id INTEGER NOT NULL, ring_s INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rec_rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  match_called TEXT NOT NULL DEFAULT '', match_caller TEXT NOT NULL DEFAULT '', schedule TEXT NOT NULL DEFAULT '',
  agent_id INTEGER NOT NULL DEFAULT 0, audio INTEGER NOT NULL DEFAULT 0, transcript INTEGER NOT NULL DEFAULT 0,
  ai_audio INTEGER NOT NULL DEFAULT 0, stop_cmd INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS call_shares(
  call_id INTEGER NOT NULL, user_id INTEGER NOT NULL, by_user INTEGER, ts REAL,
  PRIMARY KEY(call_id, user_id)
);
CREATE TABLE IF NOT EXISTS mail_recipients(
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', numbers TEXT NOT NULL DEFAULT '',
  on_call TEXT NOT NULL DEFAULT 'off', detail TEXT NOT NULL DEFAULT 'short', daily INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS usage_extra(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, model TEXT,
  in_text INTEGER, in_audio INTEGER, out_text INTEGER, out_audio INTEGER, think INTEGER, cost_usd REAL
);
CREATE TABLE IF NOT EXISTS segments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id INTEGER, ts REAL, speaker TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS call_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id INTEGER NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS assistant_changes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, user_id INTEGER, username TEXT NOT NULL DEFAULT '', area TEXT NOT NULL,
  target_id INTEGER, title TEXT NOT NULL DEFAULT '', before_json TEXT, after_json TEXT, undone_ts REAL, undone_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS anomalies(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, last_ts REAL NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL UNIQUE,
  text TEXT NOT NULL DEFAULT '', call_id INTEGER, count INTEGER NOT NULL DEFAULT 1, ack INTEGER NOT NULL DEFAULT 0,
  ack_by TEXT NOT NULL DEFAULT '', ack_ts REAL, mail INTEGER NOT NULL DEFAULT 1, mailed INTEGER NOT NULL DEFAULT 0
);
"""


def conn():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.create_function("digits", 1, lambda t: "".join(c for c in (t or "") if c.isdigit()))
        _conn.execute("PRAGMA journal_mode=WAL")      # Lesen blockiert Schreiben nicht
        _conn.execute("PRAGMA synchronous=NORMAL")
        # Zugangsdaten, Gespraeche und Aufnahmen: nur fuer den Eigentuemer (Asterisk braucht davon nichts; es liest sounds/ und schreibt rec-in/)
        d = os.path.dirname(DB_PATH)
        for p, mode in ((DB_PATH, 0o600), (DB_PATH + "-wal", 0o600), (DB_PATH + "-shm", 0o600),
                        (os.path.join(d, "rec"), 0o700), (os.path.join(d, "backups"), 0o700)):
            try:
                if os.path.exists(p):
                    os.chmod(p, mode)
            except OSError:
                pass
        _conn.executescript(SCHEMA)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_segments_call ON segments(call_id)")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_call_events_call ON call_events(call_id)")      # Ablauf je Anruf (flow.py)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started)")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_anomalies_call ON anomalies(call_id)")      # Auffaelligkeiten je Anruf (anomaly.py)
        _conn.execute("DROP TABLE IF EXISTS call_access")      # alte Freigabetabelle; neu: call_shares (einzelne Anrufe an Benutzer freigeben)
        for table, col, decl in MIGRATIONS:
            cols = [r[1] for r in _conn.execute(f"PRAGMA table_info({table})")]
            if col not in cols:
                _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                if col == "record_ai":               # bisher galt "aufzeichnen" auch fuer KI-Gespraeche
                    _conn.execute("UPDATE agents SET record_ai=record WHERE mode='assist'")
                if col == "transcribe":              # bisher gehoerte das Transkript zur Aufzeichnung
                    _conn.execute("UPDATE agents SET transcribe=1 WHERE record=1")
                if col == "inc_transcript":          # Altbestand: "mit Transkript" (detail=full) uebernehmen
                    _conn.execute("UPDATE mail_recipients SET inc_transcript=1 WHERE detail='full'")
                if col == "number_admin":            # Altbestand: vorhandene Nummern gelten als vom Administrator eingetragen
                    _conn.execute("UPDATE staff SET number_admin=number")
        # Datenschutz: der Zeitpunkt "zuletzt im WLAN" wird nicht mehr gespeichert (presence.py merkt ihn nur im Speicher)
        _conn.execute("UPDATE staff SET presence_seen=0 WHERE presence_seen!=0")
        # Altbestand: frueher importierte Kontakte trugen die Notiz "Fritzbox"
        _conn.execute("UPDATE contacts SET source='fritz', note='' WHERE note='Fritzbox' AND source='manual'")
        _conn.commit()
        for old in OLD_NOTICES:   # frühere Standardtexte auf den neuen kurzen Text umstellen
            _conn.execute("UPDATE agents SET notice=? WHERE notice=?", (DEFAULT_NOTICE, old))
        _conn.commit()
        if not _conn.execute("SELECT 1 FROM meta WHERE key='notice_default'").fetchone():
            _conn.execute("UPDATE agents SET notice=? WHERE mode='assist' AND notice=''", (DEFAULT_NOTICE,))
            _conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('notice_default','1')")
            _conn.commit()
        # Standard-Agent nur einmal anlegen (wer alle Agenten loescht, will keine KI am Telefon)
        if not _conn.execute("SELECT 1 FROM meta WHERE key='seeded'").fetchone():
            if _conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0:
                seed(_conn)
            _conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('seeded','1')")
            _conn.commit()
        _migrate_rules(_conn)
        _migrate_rules_v2(_conn)
        _migrate_rules_out(_conn)
    return _conn


def _insert_rec_rules(c, a):
    """Aufzeichnungsregeln aus den Einstellungen eines Agenten: eine an den Agenten gebundene Regel (gilt, wenn er fuehrt) und bei
    'immer aufzeichnen' zusaetzlich eine Regel ohne Agent mit seinen Bedingungen."""
    flags = (int(bool(a.get("record"))), int(bool(a.get("transcribe"))), int(bool(a.get("record_ai"))), int(a.get("stop_cmd", 1) != 0))
    sql = "INSERT INTO rec_rules(name,enabled,match_called,match_caller,schedule,agent_id,audio,transcript,ai_audio,stop_cmd) VALUES(?,?,?,?,?,?,?,?,?,?)"
    c.execute(sql, ("Aufzeichnung: " + a["name"], 1, "", "", "", a["id"], *flags))
    if a.get("rec_always"):
        c.execute(sql, ("Immer aufzeichnen: " + a["name"], int(bool(a["enabled"])), a["match_called"], a["match_caller"], a["schedule"] or "", 0, *flags))


def _migrate_rules_v2(c):
    """Nachtrag fuer bereits migrierte Datenbanken: gebundene Regel auch fuer 'immer aufzeichnen'-Agenten, Regeln mit Agent immer aktiv."""
    if c.execute("SELECT 1 FROM meta WHERE key='rules_migrated_v2'").fetchone():
        return
    c.execute("UPDATE rec_rules SET enabled=1 WHERE agent_id!=0")
    for a in c.execute("SELECT * FROM agents WHERE rec_always=1").fetchall():
        a = dict(a)
        if (a.get("record") or a.get("transcribe") or a.get("record_ai")) and not c.execute("SELECT 1 FROM rec_rules WHERE agent_id=?", (a["id"],)).fetchone():
            c.execute("INSERT INTO rec_rules(name,enabled,match_called,match_caller,schedule,agent_id,audio,transcript,ai_audio,stop_cmd) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      ("Aufzeichnung: " + a["name"], 1, "", "", "", a["id"], int(bool(a.get("record"))), int(bool(a.get("transcribe"))),
                       int(bool(a.get("record_ai"))), int(a.get("stop_cmd", 1) != 0)))
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('rules_migrated_v2','1')")
    c.commit()


def _migrate_rules_out(c):
    """Einmalig: die bisher feste Aufzeichnung ausgehender Gespraeche (Vorwahl, z. B. 000) wird eine Aufzeichnungsregel mit demselben Verhalten
    (Audio je nach Einstellung 'Audio dauerhaft speichern', Transkript, Hinweis-Ansage je nach Einstellung)."""
    if c.execute("SELECT 1 FROM meta WHERE key='rules_out'").fetchone():
        return
    keep = (c.execute("SELECT value FROM meta WHERE key='keep_audio'").fetchone() or ["0"])[0] == "1"
    notice = (c.execute("SELECT value FROM meta WHERE key='rec_notice'").fetchone() or ["1"])[0] != "0"
    c.execute("INSERT INTO rec_rules(name,enabled,match_called,match_caller,schedule,agent_id,audio,transcript,ai_audio,stop_cmd,direction,notice) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("Ausgehende Gespräche (Vorwahl)", 1, "", "", "", 0, int(keep), 1, 0, 1, "out", int(notice)))
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('rules_out','1')")
    c.commit()


def _migrate_rules(c):
    """Einmalig: aus den bisherigen Agenten-Einstellungen (Auswahl, Annahmezeit, Aufzeichnung) Anrufregeln und Aufzeichnungsregeln
    erzeugen, die dasselbe Verhalten ergeben. Die alten Spalten bleiben unveraendert in der Datenbank."""
    if c.execute("SELECT 1 FROM meta WHERE key='rules_migrated'").fetchone():
        return
    rows = c.execute("SELECT * FROM agents ORDER BY priority, id").fetchall()
    for a in rows:
        a = dict(a)
        kw_only = bool(a.get("keyword_only")) and bool((a.get("keywords") or "").strip())
        if not kw_only:             # nur per Codewort erreichbare Agenten wurden nie von selbst gewaehlt
            c.execute("INSERT INTO call_rules(name,enabled,priority,match_called,match_caller,schedule,agent_id,ring_s) VALUES(?,?,?,?,?,?,?,?)",
                      (a["name"], 1, a["priority"], a["match_called"], a["match_caller"], a["schedule"] or "", a["id"], int(a.get("answer_delay") or 0)))
        if a.get("record") or a.get("transcribe") or a.get("record_ai"):
            _insert_rec_rules(c, a)
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('rules_migrated','1')")
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('rules_migrated_v2','1')")
    c.commit()


def seed(c):
    c.execute(
        "INSERT INTO agents(name,priority,prompt,greeting,notice) VALUES(?,?,?,?,?)",
        ("Assistent", 1000,
         "Du bist ein freundlicher Telefonassistent. Sprich Deutsch, antworte kurz "
         "und natuerlich, wie in einem Telefongespraech. Wenn das Gespraech beendet "
         "ist, rufe end_call auf.",
         "Begruesse den Anrufer kurz und frage, wie du helfen kannst.", DEFAULT_NOTICE))
    c.commit()


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def x(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        conn().commit()
        return cur.lastrowid


def list_call_rules(enabled_only=False):
    return q("SELECT * FROM call_rules" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY priority, id")


def list_rec_rules(enabled_only=False):
    return q("SELECT * FROM rec_rules" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id")


def get_agent(aid):
    r = q("SELECT * FROM agents WHERE id=?", (aid,))
    return r[0] if r else None


def list_agents(enabled_only=False):
    sql = "SELECT * FROM agents" + (" WHERE enabled=1" if enabled_only else "")
    return q(sql + " ORDER BY priority, id")


def save_agent(data, agent_id=None):
    vals = []
    for f in AGENT_FIELDS:
        v = data.get(f, DEFAULTS.get(f, ""))
        if f in INT_FIELDS:
            v = int(v) if v not in ("", None) else DEFAULTS.get(f, 0)
        elif f == "tools" and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        vals.append(v)
    if agent_id is None:
        return x(f"INSERT INTO agents({','.join(AGENT_FIELDS)}) VALUES({','.join('?'*len(AGENT_FIELDS))})", vals)
    # nur mitgeschickte Felder schreiben: die Oberflaeche sendet die alten Spalten (record, answer_delay ...) nicht mehr
    keep = [(f, v) for f, v in zip(AGENT_FIELDS, vals) if f in data]
    x(f"UPDATE agents SET {','.join(f + '=?' for f, _ in keep)} WHERE id=?", [v for _, v in keep] + [agent_id])
    return agent_id


def list_templates():
    return q("SELECT * FROM templates ORDER BY name")


def save_template(d, tid=None):
    vals = [(d.get("name") or "").strip(), (d.get("task") or "").strip(), d.get("agent_id") or None]
    if tid is None:
        return x("INSERT INTO templates(name,task,agent_id) VALUES(?,?,?)", vals)
    x("UPDATE templates SET name=?, task=?, agent_id=? WHERE id=?", vals + [tid])
    return tid


STAFF_STATUS = labels.STAFF_STATUS          # Texte: labels.py (eine Stelle, auch fuer die Oberflaeche)
KINDS = labels.STAFF_KIND                   # person | group (Sammelziel)


def caller_status(st):
    """Statustext fuer die KI und damit fuer Anrufer: "nicht stoeren" erscheint als "nicht erreichbar" (die KI soll nicht verraten,
    dass die Person da ist, aber nicht gestoert werden moechte)."""
    return STAFF_STATUS["off" if st == "dnd" else st]


def clean_dnd_allow(v):
    """Ausnahmen bei "nicht stoeren": Rufnummern (Komma/Zeilen), nur Ziffern und +, hoechstens 30. Ein * am Ende steht fuer alle
    Nummern mit diesem Anfang (z. B. 0221555* = alle Durchwahlen einer Firma); dafuer mindestens 4 Ziffern, damit nicht versehentlich
    ein ganzes Ortsnetz oder alle Anrufer durchkommen."""
    out = []
    for raw in re.split(r"[,;\n]", str(v or "")):
        wild = raw.strip().endswith("*")
        p = "".join(c for c in raw if c.isdigit() or c == "+")
        if len(p.lstrip("+")) < (4 if wild else 3):
            continue
        p += "*" if wild else ""
        if p not in out:
            out.append(p)
    return ",".join(out[:30])


def clean_see_staff(v, sid=None):
    """IDs vorhandener Mitarbeiter/Sammelziele (ohne sich selbst), deren Anrufe jemand zusaetzlich sieht."""
    known = {r["id"] for r in q("SELECT id FROM staff")}
    out = []
    for p in re.split(r"[,;\s]+", ",".join(map(str, v)) if isinstance(v, list) else str(v or "")):
        if p.isdigit() and int(p) in known and int(p) != sid and int(p) not in out:
            out.append(int(p))
    return ",".join(map(str, out))


def dnd_allows(s, caller):
    """Darf dieser Anrufer trotz "nicht stoeren" durchgestellt werden? (Nummer steht in den Ausnahmen, jede Schreibweise)"""
    from .dialrules import _same            # gleiche Nummer (jede Schreibweise) oder "Anfang*"
    return bool(caller) and any(_same(caller, n) for n in (s.get("dnd_allow") or "").split(",") if n)


def reach_number(s, caller="", called=""):
    """Nummer, unter der der Mitarbeiter selbst fuer diesen Anrufer gerade erreichbar ist ('' = nicht erreichbar), ohne Vertretung.
    Gleiche Rangfolge wie resolve() (nicht stoeren > Waehlregel > Zeitplan > Status)."""
    r = resolve(s, caller, called, _deputy=False)
    return "" if r["ai"] else r["number"]


def is_group(s):
    """Sammelziel (z. B. Zentrale): nur Nummern, kein Status, keine Anwesenheit, kein Konto."""
    return (s.get("kind") or "person") == "group"


def _now_local():
    import datetime
    import zoneinfo
    return datetime.datetime.now(zoneinfo.ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))).replace(tzinfo=None)


def global_rules():
    """Zeitplan der Geschaeftszeiten; alter Text ('Mo-Fr 08:00-17:00') wird als Zeilen gelesen."""
    rules = sched.loads(get_meta("schedule", ""))
    return rules or sched.from_legacy(get_meta("hours", "") or "")


def staff_rules(s):
    rules = sched.loads(s.get("schedule") or "")
    return rules or sched.from_legacy(s.get("hours") or "")


def business_state(now=None):
    """{'configured', 'open', 'status', 'note'}; ohne Zeitplan immer geoeffnet."""
    rules = global_rules()
    if not rules:
        return {"configured": False, "open": True, "status": "office", "note": ""}
    ev = sched.evaluate(rules, now or _now_local())
    return {"configured": True, "open": ev["status"] == "office", "status": ev["status"], "note": ev["note"]}


def business_open():
    """Gilt die Geschaeftszeit gerade? Ohne hinterlegte Zeiten immer True."""
    return business_state()["open"]


def list_staff(effective=True):
    """Mitarbeiter. 'bis' erreicht -> wieder im Buero. effective=True: ausserhalb der Arbeitszeit (falls beachtet)
    gilt 'im Buero' als 'nicht erreichbar'; effective=False liefert den gespeicherten Status plus eff_status."""
    rows = q("SELECT * FROM staff ORDER BY name")
    now = _now_local().strftime("%Y-%m-%dT%H:%M")
    for s in rows:
        if is_group(s):
            s["status"] = "office"               # Sammelziel: kein Status
        if s["status"] != "office" and s["until"] and s["until"] <= now:
            x("UPDATE staff SET status='office', status_src='until', note='', until='' WHERE id=?", (s["id"],))
            s.update(status="office", status_src="until", note="", until="")
    glob = global_rules()
    nowl = _now_local()
    for s in rows:
        s["status_raw"] = s["status"]           # gespeicherter Status (resolve() rechnet damit, auch bei effective=True)
        s["eff_status"], s["auto_note"] = s["status"], ""
        s["rules"] = staff_rules(s)
        if s["status"] in ("office", "dnd"):     # Zeitplan gilt fuer "erreichbar" und "nicht stoeren" (Ausnahmen nachts nicht durch)
            ev = schedule_state(s, nowl, glob)
            if ev and ev["status"] != "office":
                # abwesend -> Weiterleitung "ausser Haus"; nicht erreichbar / ausserhalb -> nicht erreichbar
                s["eff_status"] = "away" if ev["status"] == "away" else "off"
                s["auto_note"] = ev["note"] or sched.STATUSES[ev["status"]]
        if effective and s["eff_status"] != s["status"]:
            s["status"], s["note"] = s["eff_status"], s["note"] or s["auto_note"]
    return rows


def _presence_fields(d, sid, presence_self=False):
    """Anwesenheit ueber WLAN-Geraet (presence.py): Schalter und MAC-Adresse, nur wenn mitgeschickt. Einschalten nur durch die Person
    selbst (presence_self, PUT /api/me/staff); andere duerfen nur ausschalten bzw. eingeschaltet lassen."""
    from . import presence
    if "presence_mac" in d:
        x("UPDATE staff SET presence_mac=? WHERE id=?", (presence.clean_macs(d["presence_mac"]), sid))
    if "presence_on" in d:
        on = 1 if d["presence_on"] else 0
        if not on or presence_self:      # einschalten durch andere: bleibt, wie es ist (die API meldet das vorher, presence_on_denied)
            x("UPDATE staff SET presence_on=? WHERE id=?", (on, sid))


def _remote_fields(d, sid):
    """Fernabfrage-Einstellungen (nur wenn mitgeschickt)."""
    if "remote_on" in d:
        x("UPDATE staff SET remote_on=? WHERE id=?", (1 if d["remote_on"] else 0, sid))
    for k in ("remote_numbers", "remote_codes"):
        if k in d:
            val = ",".join(p.strip() for p in str(d[k] or "").split(",") if p.strip())[:300]
            x(f"UPDATE staff SET {k}=? WHERE id=?", (val, sid))


def _cal_fields(d, sid):
    """Kalender-Einstellungen des Mitarbeiters (nur wenn mitgeschickt)."""
    if "cal_id" in d:
        x("UPDATE staff SET cal_id=? WHERE id=?", (str(d["cal_id"] or "").strip()[:200], sid))
    if "cal_detail" in d:
        x("UPDATE staff SET cal_detail=? WHERE id=?", ("busy" if d["cal_detail"] == "busy" else "full", sid))
    if "cal_reveal" in d:
        x("UPDATE staff SET cal_reveal=? WHERE id=?", (",".join(p.strip() for p in str(d["cal_reveal"] or "").split(",") if p.strip())[:300], sid))
    if "cal_reveal_staff" in d:
        x("UPDATE staff SET cal_reveal_staff=? WHERE id=?", (1 if d["cal_reveal_staff"] else 0, sid))


def clean_nicknames(v):
    """Anreden: kommagetrennt, nur Buchstaben/Leerzeichen/Bindestrich/Apostroph, hoechstens 8 je 24 Zeichen (landen im Prompt)."""
    out = []
    for p in re.split(r"[,;\n]", str(v or "")):
        p = re.sub(r"<[^>]*>", "", p)
        p = re.sub(r"[^\w .'-]", "", p, flags=re.U).strip()[:24]
        if p and p.lower() not in [o.lower() for o in out]:
            out.append(p)
    return ", ".join(out[:8])


def presence_on_denied(d, sid=None):
    """Wuerde diese Aenderung (nicht von der Person selbst) die WLAN-Anwesenheit einschalten?"""
    if not d.get("presence_on"):
        return False
    old = q("SELECT presence_on FROM staff WHERE id=?", (sid,)) if sid else []
    return not (old and old[0]["presence_on"])


REC_OUT = ("off", "record", "transcript", "numbers")


def _rec_out_fields(d, sid):
    """Mitschrift ausgehender Gespraeche (nur wenn mitgeschickt; geprueft wird vorher in main/outrec)."""
    if "rec_out" in d:
        x("UPDATE staff SET rec_out=? WHERE id=?", (d["rec_out"] if d["rec_out"] in REC_OUT else "off", sid))
    if "rec_out_numbers" in d:
        x("UPDATE staff SET rec_out_numbers=? WHERE id=?", (clean_dnd_allow(d["rec_out_numbers"]), sid))
    if "rec_out_notice" in d:
        x("UPDATE staff SET rec_out_notice=? WHERE id=?", (0 if d["rec_out_notice"] in (0, False, "0") else 1, sid))


GROUP_CLEAR = {"status": "office", "note": "", "until": "", "away_number": "", "dnd_allow": "", "presence_on": 0, "presence_mac": "",
               "rec_out": "off", "rec_out_numbers": "", "rec_out_notice": 1, "remote_on": 0, "remote_numbers": "", "remote_codes": "", "cal_id": "", "nicknames": "", "see_staff": ""}


def save_staff(d, sid=None, presence_self=False):
    """presence_self: die Person aendert ihren eigenen Eintrag (nur dann darf die WLAN-Anwesenheit eingeschaltet werden).
    kind 'group' (Sammelziel): nur Name, Nummern, Vertretung, Zeitplan/Waehlregeln - Status, Anwesenheit usw. werden geleert."""
    kind = d.get("kind") if d.get("kind") in KINDS else None
    if kind is None and sid:
        old = q("SELECT kind FROM staff WHERE id=?", (sid,))
        kind = old[0]["kind"] if old else "person"
    kind = kind or "person"
    if kind == "group":
        d = dict(d, **GROUP_CLEAR)
    status = d.get("status") if d.get("status") in STAFF_STATUS else "office"
    dep = d.get("deputy_id")
    vals = [(d.get("name") or "").strip(), (d.get("role") or "").strip(), (d.get("number") or "").strip(),
            (d.get("away_number") or "").strip(), status, (d.get("note") or "").strip()[:200],
            int(dep) if dep and int(dep) != sid else None, (d.get("until") or "").strip()[:16],
            ",".join(p for p in (",".join(d["msn"]) if isinstance(d.get("msn"), list) else (d.get("msn") or "")).replace(" ", "").split(",") if p),
            1 if d.get("record") else 0, sched.dumps(d.get("schedule")) if isinstance(d.get("schedule"), list) else (d.get("schedule") or ""),
            1 if d.get("use_hours") else 0]
    if sid is None:
        new = x("INSERT INTO staff(name,role,number,away_number,status,note,deputy_id,until,msn,record,schedule,use_hours,kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", vals + [kind])
        if "ai_fallback" in d:
            x("UPDATE staff SET ai_fallback=? WHERE id=?", (1 if d["ai_fallback"] else 0, new))
        if "nicknames" in d:
            x("UPDATE staff SET nicknames=? WHERE id=?", (clean_nicknames(d["nicknames"]), new))
        if "dnd_allow" in d:
            x("UPDATE staff SET dnd_allow=? WHERE id=?", (clean_dnd_allow(d["dnd_allow"]), new))
        if "see_staff" in d:
            x("UPDATE staff SET see_staff=? WHERE id=?", (clean_see_staff(d["see_staff"], new), new))
        _presence_fields(d, new, presence_self)
        _rec_out_fields(d, new)
        _remote_fields(d, new)
        _cal_fields(d, new)
        return new
    if "msn" not in d:                  # Rufnummern werden nur auf der Seite "Rufnummern" zugewiesen
        old = q("SELECT msn FROM staff WHERE id=?", (sid,))
        vals[8] = old[0]["msn"] if old else ""
    # anderer Status als gespeichert = von Hand gesetzt; derselbe Status (z. B. beim Speichern anderer Felder) behaelt seine Herkunft
    x("UPDATE staff SET status_src='manual' WHERE id=? AND status!=?", (sid, status))
    x("UPDATE staff SET name=?,role=?,number=?,away_number=?,status=?,note=?,deputy_id=?,until=?,msn=?,record=?,schedule=?,use_hours=?,kind=?,hours='' WHERE id=?", vals + [kind, sid])
    if "ai_fallback" in d:
        x("UPDATE staff SET ai_fallback=? WHERE id=?", (1 if d["ai_fallback"] else 0, sid))
    if "nicknames" in d:
        x("UPDATE staff SET nicknames=? WHERE id=?", (clean_nicknames(d["nicknames"]), sid))
    if "dnd_allow" in d:
        x("UPDATE staff SET dnd_allow=? WHERE id=?", (clean_dnd_allow(d["dnd_allow"]), sid))
    if "see_staff" in d:
        x("UPDATE staff SET see_staff=? WHERE id=?", (clean_see_staff(d["see_staff"], sid), sid))
    _presence_fields(d, sid, presence_self)
    _rec_out_fields(d, sid)
    _remote_fields(d, sid)
    _cal_fields(d, sid)
    return sid


def end_status(call_id):
    """Endstatus eines Anrufs mit Aufnahme/Mitschrift: 'beendet' (Aufnahme-Anruf), 'durchgestellt' (die KI hat gesprochen und durchgestellt),
    'angenommen' (eine Person hat abgenommen) oder 'unbeantwortet' (niemand hat abgenommen, z. B. Anrufer legt waehrend der Musik auf)."""
    r = q("SELECT direction FROM calls WHERE id=?", (call_id,))
    d = r[0]["direction"] if r else ""
    if d == "rec":
        return "beendet"
    if d != "in":
        return "durchgestellt"
    if q("SELECT 1 FROM segments WHERE call_id=? AND speaker='agent' LIMIT 1", (call_id,)):
        return "durchgestellt"
    if q("SELECT 1 FROM call_events WHERE call_id=? AND (kind='answered' OR (kind='rec' AND data LIKE '%start%')) LIMIT 1", (call_id,)):
        return "angenommen"
    return "unbeantwortet"


def _digits(t):
    return "".join(c for c in (t or "") if c.isdigit())


def same_number(a, b):
    """Gleiche Rufnummer? Kurze (interne) Nummern exakt, sonst national und vollstaendig (Rueckfall letzte 9 Ziffern nur ohne
    Vorwahl): dialrules.same_number."""
    return drules.same_number(a, b)


def number_key(n):
    """Vergleichsschluessel wie same_number: kurze (interne) Nummern ganz, sonst die letzten 9 Ziffern; leer = unterdrueckt."""
    d = _digits(n)
    return d if len(d) < 7 else d[-9:]


def day_positions(ids, scope_sql="1=1", scope_args=()):
    """Je eingehendem Anruf (id): (n, gesamt) = der wievielte Anruf dieser Nummer an seinem Tag (Ortszeit) und wie viele es an dem
    Tag insgesamt waren. Nur Anrufe, die der Benutzer sehen darf (scope_sql); unterdrueckte Nummern zaehlen nie."""
    if not ids:
        return {}
    day = "date(started,'unixepoch','localtime')"
    mark = ",".join("?" * len(ids))
    days = {r["d"] for r in q(f"SELECT DISTINCT {day} AS d FROM calls WHERE id IN ({mark}) AND direction='in'", list(ids)) if r["d"]}
    if not days:
        return {}
    # Zeitraum ueber started (Index idx_calls_started) statt date(started) ueber die ganze Tabelle. Die Tagesgrenzen rechnet SQLite
    # selbst (Ortszeit -> UTC, mit Sommerzeit), damit sie genau zu 'localtime' oben passen; danach genau auf die Tage filtern
    b = q("SELECT CAST(strftime('%s', ?, 'utc') AS REAL) AS lo, CAST(strftime('%s', ?, '+1 day', 'utc') AS REAL) AS hi",
          (min(days), max(days)))[0]
    lo, hi = b["lo"] - 3600, b["hi"] + 3600
    rows = q(f"SELECT id, caller, {day} AS d FROM calls WHERE started>=? AND started<? AND direction='in' "
             f"AND ({scope_sql}) ORDER BY started, id", [lo, hi] + list(scope_args))
    groups = {}
    for r in rows:
        if r["d"] not in days:
            continue
        k = number_key(r["caller"])
        if k:
            groups.setdefault((r["d"], k), []).append(r["id"])
    out = {}
    for g in groups.values():
        for i, cid in enumerate(g, 1):
            out[cid] = (i, len(g))
    return out


OUT_HANGUP = ("aufgelegt, ohne mit jemandem zu sprechen", "nicht angenommen")
REPEAT_KINDS = {"": "alle Anrufe", "hangup": "nur aufgelegte (niemanden erreicht)", "nomsg": "alle ohne Nachricht"}


def repeat_counts(outcome, kind):
    """Zaehlt ein frueherer Anruf mit diesem Ausgang fuer die Regel "mehrfach angerufen"?"""
    if kind == "hangup":
        return outcome in OUT_HANGUP
    if kind == "nomsg":
        return outcome != "Nachricht hinterlassen"
    return True


def call_outcome(row):
    """Wie ist ein frueherer eingehender Anruf ausgegangen? (kurzer Text fuer den Agenten)"""
    try:
        notes = json.loads(row.get("notes") or "{}")
    except ValueError:
        notes = {}
    if any(str(k).startswith("Nachricht") for k in notes):
        return "Nachricht hinterlassen"
    st = row.get("status") or ""
    if st == "transkription fehlgeschlagen":
        st = end_status(row["id"])
    if st in ("angenommen", "durchgestellt"):
        return "mit jemandem gesprochen"
    if st == "beendet":
        return "mit der KI gesprochen, keine Nachricht"
    if st in ("aktiv", "waehlt"):
        return "läuft noch"
    if st == "abgelehnt":
        return "nicht angenommen"
    return "aufgelegt, ohne mit jemandem zu sprechen"


def recent_calls_from(caller, minutes, exclude_uuid=None):
    """Fruehere eingehende Anrufe dieser Nummer in den letzten 'minutes' Minuten, aelteste zuerst:
    [{'started', 'outcome'}]. Unterdrueckte Nummern zaehlen nie."""
    if not _digits(caller) or minutes <= 0:
        return []
    rows = q("SELECT id, uuid, started, caller, status, notes FROM calls WHERE direction='in' AND started>=? ORDER BY started",
             (time.time() - minutes * 60,))
    return [{"started": r["started"], "outcome": call_outcome(r)} for r in rows
            if r["uuid"] != exclude_uuid and same_number(r["caller"], caller)]


def number_owners():
    """{Ziffern der Rufnummer: [Mitarbeiter, ...]} - eine Nummer kann einem (exklusiv) oder mehreren (geteilt) gehoeren."""
    out = {}
    for s in q("SELECT * FROM staff ORDER BY name"):
        for m in (s["msn"] or "").split(","):
            d = _digits(m)
            if d and s not in out.setdefault(d, []):
                out[d].append(s)
    return out


def staffs_for_number(called_values):
    """Alle Mitarbeiter, denen die angerufene Fritzbox-Rufnummer zugewiesen ist (Ziffernvergleich)."""
    called = [_digits(c) for c in called_values if _digits(c)]
    msns = [(s, _digits(m)) for s in list_staff() for m in (s["msn"] or "").split(",") if _digits(m)]
    fits = lambda c, m: c == m or (len(m) >= 5 and c.endswith(m))
    # je angerufener Nummer nur die laengste passende MSN (exakt zuerst): bei 12345 und 912345 gehoert ...912345 nicht zu 12345
    best = {c: max((len(m) for _, m in msns if fits(c, m)), default=0) for c in called}
    out = []
    for s, m in msns:
        if any(best[c] and len(m) == best[c] and fits(c, m) for c in called) and s not in out:
            out.append(s)
    return out


def msn_since_key(sid, number):
    return f"msn_since:{sid}:{_digits(number)}"


def msn_numbers(sid):
    """[(Ziffern, seit)] der dem Mitarbeiter zugewiesenen Rufnummern; seit = Zeitpunkt der Zuweisung (0 = schon immer, vor
    Einfuehrung dieser Angabe). Benutzer sehen Anrufe auf eine Nummer erst ab ihrer Zuweisung."""
    r = q("SELECT msn FROM staff WHERE id=?", (sid,)) if sid else []
    out = []
    for m in (r[0]["msn"] if r else "").split(","):
        d = _digits(m)
        if d:
            try:
                since = float(get_meta(msn_since_key(sid, d), "0") or 0)
            except ValueError:
                since = 0.0
            out.append((d, since))
    return out


def visible_numbers(sid):
    """Rufnummern, deren Anrufe der Benutzer dieses Mitarbeiters sieht: die eigenen und die der Mitarbeiter/Sammelziele aus
    "sieht auch Anrufe von" (see_staff), jeweils ab deren Zuweisung. [(Ziffern, seit)]"""
    out = list(msn_numbers(sid))
    r = q("SELECT see_staff FROM staff WHERE id=?", (sid,)) if sid else []
    for o in (r[0]["see_staff"] if r else "").split(","):
        if o.isdigit() and int(o) != sid:
            for d, since in msn_numbers(int(o)):
                hit = next((i for i, (n, _) in enumerate(out) if n == d), None)
                if hit is None:
                    out.append((d, since))
                elif since < out[hit][1]:
                    out[hit] = (d, since)
    return out


def canonical_msn(called):
    """Zugewiesene Rufnummer (Ziffern), die zur angerufenen Nummer passt (auch bei abweichendem Format der Fritzbox, z. B. mit Vorwahl),
    sonst ''. Damit steht in der Anrufliste dieselbe Nummer, die Sichtbarkeit, Fernabfrage und Mail-Filter vergleichen."""
    c = _digits(called)
    if not c:
        return ""
    best = ""
    for s in q("SELECT msn FROM staff WHERE msn!=''"):
        for m in (s["msn"] or "").split(","):
            m = _digits(m)
            if m and (c == m or (len(m) >= 5 and c.endswith(m))) and len(m) > len(best):
                best = m              # laengster Treffer (12345 und 912345: ...912345 ist 912345)
    return best


def dialrules_of(s):
    """Gespeicherte Waehlregeln eines Mitarbeiters (Liste)."""
    try:
        return json.loads(s.get("dialrules") or "[]")
    except ValueError:
        return []


RESOLVE_RULES = """Rangfolge (eine Stelle fuer Klingeln, Verbinden durch die KI, Mitarbeiterliste der KI und Vorschau):
  1. "nicht stoeren" (dnd): nur Anrufer aus den Ausnahmen (dnd_allow) kommen weiter, alle anderen gelten als nicht erreichbar
     - auch eine Waehlregel "erreichbar" holt sie nicht durch.
  2. Waehlregel: die erste passende aktive Regel entscheidet (erreichbar / abwesend / nicht erreichbar / weiterleiten / KI).
  3. Zeitplan (use_hours): gilt fuer "erreichbar" und "nicht stoeren"; sagt er abwesend -> Nummer ausser Haus,
     nicht erreichbar / ausserhalb der Buerozeit -> nicht erreichbar (auch fuer Ausnahmen; durch kommt dann nur,
     wem eine Waehlregel das ausdruecklich erlaubt).
  4. Manueller Status: erreichbar -> Nummer im Buero, ausser Haus -> Nummer ausser Haus, nicht erreichbar -> keine;
     Ausnahme bei "nicht stoeren" -> Nummer im Buero, sonst ausser Haus.
  Ist die Person danach nicht erreichbar (ausser bei der Regel "direkt zum Anrufbeantworter"), gilt die Vertretung, sofern diese
  fuer denselben Anrufer erreichbar ist (nach denselben Regeln, ohne weitere Vertretung). Sammelziele (z. B. Zentrale) haben keinen
  Status: sie sind immer unter ihren Nummern erreichbar (Waehlregeln und Zeitplan gelten auch fuer sie)."""


def resolve(s, caller="", called="", now=None, staff=None, answer_now="", _deputy=True):
    """Kann dieser Anrufer den Mitarbeiter s jetzt erreichen, unter welcher Nummer und warum? (Rangfolge: RESOLVE_RULES)
    answer_now: Name der Anrufregel, die sofort ohne Klingeln an die KI geht (gilt, wenn keine Waehlregel passt).
    Ergebnis: number ('' = nicht erreichbar), target (Mitarbeiter oder Vertretung), via_deputy, ai (sofort zur KI), rule (Waehlregel),
    status (fuer diesen Anrufer: office/away/off/dnd), note (Grund fuer den Anrufer), blocked (durch "nicht stoeren" oder eine Regel
    gesperrt), reason (Begruendung als Text), steps (Begruendung in Schritten)."""
    now = now or _now_local()
    group = is_group(s)
    raw = "office" if group else (s.get("status_raw") or s.get("status") or "office")
    res = {"number": "", "target": s, "via_deputy": False, "ai": False, "rule": None, "status": raw, "note": s.get("note") or "",
           "blocked": False, "steps": []}
    steps = res["steps"]
    rule = None
    if raw == "dnd" and not dnd_allows(s, caller):
        res.update(status="dnd", blocked=True)
        steps.append("„nicht stören“ – der Anrufer steht nicht in den Ausnahmen")
        if answer_now:                   # die KI nimmt ohnehin sofort an: auch keine Vertretung klingeln lassen
            steps.append(f"Anrufregel „{answer_now}“ nimmt sofort an (ohne Klingeln)")
            res["ai"] = True
            return _finish(res)
    else:
        if raw == "dnd":
            res["note"] = ""             # der Grund fuer "nicht stoeren" geht Ausnahmen nichts an
            steps.append("„nicht stören“, aber der Anrufer ist eine Ausnahme")
        rules = dialrules_of(s)
        rule = drules.first_match(rules, caller, _digits(called), now, lambda n: find_contact(n) is not None) if rules else None
        if rule:
            a = rule["action"]
            res["rule"] = rule
            res["note"] = rule.get("note") or res["note"]
            steps.append(f"Wählregel „{rule['name']}“: {drules.ACTIONS[a]}" + (f" {rule['target']}" if a == "forward" else ""))
            if a == "ai":
                res.update(ai=True, status="off", blocked=True)
                return _finish(res)
            if a == "forward":
                res.update(number=rule["target"], status="office")
            elif a == "office":
                res.update(number=s.get("number") or "", status="office")
            elif a == "away":
                res.update(number=s.get("away_number") or "", status="away")
            else:
                res.update(status="off", blocked=True)
        elif answer_now:
            steps.append(f"Anrufregel „{answer_now}“ nimmt sofort an (ohne Klingeln)")
            res.update(ai=True, status="off")
            return _finish(res)
        else:
            base = raw
            ev = schedule_state(s, now) if raw in ("office", "dnd") else None
            if ev and ev["status"] != "office":
                base = "away" if ev["status"] == "away" else "off"
                res["note"] = res["note"] or ev["note"] or sched.STATUSES[ev["status"]]
                steps.append(f"Zeitplan: {sched.STATUSES[ev['status']]}" + (f" ({ev['note']})" if ev["note"] else ""))
            elif not group:
                steps.append(f"Status: {STAFF_STATUS[raw]}")
            if base == "office":
                res.update(number=s.get("number") or "", status="office")
            elif base == "dnd":
                res.update(number=s.get("number") or s.get("away_number") or "", status="office")
            elif base == "away":
                res.update(number=s.get("away_number") or "", status="away")
            else:
                res["status"] = "off"
    if not res["number"]:
        if not res["blocked"] and res["status"] in ("office", "away"):
            steps.append("keine Nummer außer Haus hinterlegt" if res["status"] == "away" else "keine Nummer hinterlegt")
        dep = None
        if _deputy and s.get("deputy_id"):
            dep = next((x for x in (staff if staff is not None else list_staff()) if x["id"] == s["deputy_id"]), None)
        if dep:
            r2 = resolve(dep, caller, called, now, staff, _deputy=False)
            if r2["number"]:
                res.update(number=r2["number"], target=dep, via_deputy=True)
                steps.append(f"Vertretung {dep['name']}: " + "; ".join(r2["steps"]))
            else:
                steps.append(f"Vertretung {dep['name']} ist auch nicht erreichbar")
    return _finish(res)


def _finish(res):
    res["reason"] = "; ".join(res["steps"])
    return res


def schedule_state(s, now=None, glob=None):
    """Zeitplan eines Mitarbeiters (eigener, sonst die Geschaeftszeiten), wenn er ihn anwendet: {'status', 'note', 'matched'}, sonst None."""
    if not s.get("use_hours"):
        return None
    rules = staff_rules(s) or (glob if glob is not None else global_rules())
    return sched.evaluate(rules, now or _now_local()) if rules else None


def route_text(res, s, caller_label):
    """Vorschau als Satz: 'Ein Anruf von X ginge jetzt an ..., weil ...'."""
    if res["ai"] or not res["number"]:
        where = "direkt an den Anrufbeantworter (KI)" if res["ai"] else "an niemanden direkt (die KI bzw. das Klingeln aller Telefone übernimmt)"
    elif res["via_deputy"]:
        where = f"an die Vertretung {res['target']['name']} ({res['number']})"
    else:
        where = f"an {s['name']} ({res['number']})"
    return f"Ein Anruf von {caller_label} ginge jetzt {where}, weil: {res['reason'] or 'keine Angaben'}."


def staff_route(s, staff=None, caller="", called=""):
    """Wohin soll ein Anruf fuer diesen Mitarbeiter? (Nummer, Mitarbeiter, ueber_vertretung) oder None. Duenne Huelle um resolve()."""
    r = resolve(s, caller, called, staff=staff)
    return (r["number"], r["target"], r["via_deputy"]) if r["number"] else None


def _norm(t):
    t = (t or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    return [w for w in "".join(c if c.isalnum() else " " for c in t).split()
            if w not in ("herr", "frau", "dr", "prof", "hr", "fr")]


_FILLER = {"mobil", "handy", "mobiltelefon", "privat", "festnetz", "nummer", "telefon", "anschluss"}


def _wmatch(w, n, fuzzy=False):
    """Wort aus der Anfrage passt zu einem Namenswort: Teilstring (exakt) oder, wenn fuzzy, aehnlich geschrieben."""
    if w in n or n in w:
        return True
    if fuzzy and len(w) >= 4 and len(n) >= 4:
        import difflib
        r = difflib.SequenceMatcher(None, w, n).ratio()
        return r >= 0.8 or (w[:3] == n[:3] and r >= 0.6)      # Meier/Maier
    return False


def _name_hits(rows, query):
    """Treffer nach Name. Exakte Treffer haben Vorrang; nur ohne exakten Treffer zaehlen aehnlich geschriebene
    (Spracherkennung: Meier statt Maier). Existieren Meier und Maier, wird 'Meier' also nie als 'Maier' gedeutet."""
    qw = _norm(query)
    if not qw:
        return []
    cores = [qw]
    core = [w for w in qw if w not in _FILLER]
    if core and len(core) < len(qw):            # Zusaetze wie "mobil" / "handy" stehen meist nicht im Namen
        cores.append(core)
    for words in cores:
        for fuzzy in (False, True):
            hit = [r for r in rows if all(any(_wmatch(w, n, fuzzy) for n in _norm(r["name"])) for w in words)]
            if hit:
                return hit
    return []


def find_staff(query):
    """Mitarbeiter nach gesprochenem Namen suchen (Anrede/Umlaute egal, aehnlich geschriebene Namen passen)."""
    return _name_hits(list_staff(), query)


def list_contacts():
    return q("SELECT * FROM contacts ORDER BY name")


SALUTATIONS = ("", "Herr", "Frau")


def save_contact(d, cid=None):
    sal = d.get("salutation") if d.get("salutation") in SALUTATIONS else ""
    vals = [d.get("name", ""), d.get("number", ""), d.get("note", ""), sal]
    if cid is None:
        return x("INSERT INTO contacts(name,number,note,salutation) VALUES(?,?,?,?)", vals)
    x("UPDATE contacts SET name=?,number=?,note=?,salutation=? WHERE id=?", vals + [cid])
    return cid


def get_meta(key, default=""):
    r = q("SELECT value FROM meta WHERE key=?", (key,))
    return r[0]["value"] if r else default


def set_meta(key, value):
    x("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, str(value)))


def find_contacts_by_name(query, limit=6):
    """Kontakte nach gesprochenem Namen (Anrede/Umlaute egal, aehnlich geschriebene Namen passen)."""
    return _name_hits(list_contacts(), query)[:limit]


def find_contact(number):
    """Kontakt zur Anrufernummer. Kurze (interne) Nummern exakt, sonst die letzten
    9 Ziffern (Vorwahl 0171 und +49171 sind dieselbe Nummer)."""
    n = "".join(c for c in (number or "") if c.isdigit())
    if not n:
        return None
    for c in list_contacts():
        m = "".join(ch for ch in c["number"] if ch.isdigit())
        if not m:
            continue
        if min(len(n), len(m)) < 7:
            if n == m:
                return c
        elif n[-9:] == m[-9:]:
            return c
    return None


def delete_call(cid):
    """Anruf samt Transkript loeschen; gibt den Pfad einer Aufnahme zurueck (Datei loescht der Aufrufer)."""
    r = q("SELECT recording FROM calls WHERE id=?", (cid,))
    x("DELETE FROM segments WHERE call_id=?", (cid,))
    x("DELETE FROM call_events WHERE call_id=?", (cid,))
    x("DELETE FROM call_shares WHERE call_id=?", (cid,))
    x("DELETE FROM calls WHERE id=?", (cid,))
    return r[0]["recording"] if r else ""


def add_segment(call_id, speaker, text, agent=""):
    x("INSERT INTO segments(call_id,ts,speaker,text,agent) VALUES(?,?,?,?,?)",
      (call_id, time.time(), speaker, text, agent))


def x_count(sql, args=()):
    """Wie x(), gibt aber die Zahl der geaenderten Zeilen zurueck (z. B. fuer atomares Buchen)."""
    with _lock:
        cur = conn().execute(sql, args)
        conn().commit()
        return cur.rowcount
