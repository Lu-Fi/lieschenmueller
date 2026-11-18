"""Aufraeumen: Statustexte an einer Stelle (labels.py, GET /api/labels), Oberflaeche ohne eigene Kopien."""
import os
import re
import tracemalloc
import uuid

import numpy as np

from app import db, dialrules, labels, main, schedule
from tests.conftest import ROOT, run

INDEX = os.path.join(ROOT, "bridge", "app", "static", "index.html")


def test_labels_served_once_and_used_by_modules(bridge):
    assert db.STAFF_STATUS is labels.STAFF_STATUS and schedule.STATUSES is labels.SCHEDULE_STATUS
    assert dialrules.ACTIONS is labels.DIALRULE_ACTIONS and dialrules.MODES is labels.DIALRULE_MODES
    assert set(labels.STAFF_STATUS) == {"office", "away", "off", "dnd"}            # gespeicherte Werte unveraendert

    async def scenario(b):
        main.app.dependency_overrides[main.user_auth] = lambda: {"id": 9, "username": "u", "role": "user", "staff_id": None, "disabled": 0}
        try:
            r = await b.http.get("/api/labels")
        finally:
            main.app.dependency_overrides.pop(main.user_auth, None)
        assert r.status_code == 200
        d = r.json()
        assert d["staff_status"]["dnd"] == "nicht stören" and d["schedule_status"]["closed"] == "außerhalb der Bürozeit"
        assert list(d["staff_status"]) == ["office", "away", "off", "dnd"] and d["staff_status_class"]["office"] == "ok"
        assert d["dialrule_actions"]["ai"] == "direkt zum Anrufbeantworter" and "status_src" in d and "staff_kind" in d
    run(bridge, scenario)


def test_ui_has_no_own_status_texts():
    html = open(INDEX, encoding="utf-8").read()
    # keine eigenen Tabellen wie {office:["erreichbar","ok"], ...} oder {office:"erreichbar", ...} mehr
    assert not re.search(r'office:\s*\[?"erreichbar', html)
    assert not re.search(r'\b(SST|MYS|SCH_ST)\b', html)
    assert 'api("/api/labels")' in html


def test_day_positions_uses_started_index_and_local_days(bridge):
    db = bridge.db

    def add(caller, local):
        # Ortszeit -> Zeitstempel ueber SQLite selbst (wie 'localtime' in der Abfrage)
        t = db.q("SELECT CAST(strftime('%s', ?, 'utc') AS REAL) AS t", (local,))[0]["t"]
        return db.x("INSERT INTO calls(uuid,started,caller,called,status,direction) VALUES(?,?,?,?,?,?)",
                    (str(uuid.uuid4()), t, caller, "0301111", "beendet", "in"))
    # Sommerzeit-Ende (25-h-Tag) und Mitternacht: 23:59 und 00:01 sind verschiedene Tage
    a = add("0170111222", "2026-10-25 23:59")
    b = add("0170111222", "2026-10-26 00:01")
    c = add("0170111222", "2026-10-25 00:30")
    sql = []
    db.conn().set_trace_callback(sql.append)
    try:
        pos = db.day_positions([a, b, c])
    finally:
        db.conn().set_trace_callback(None)
    assert pos == {c: (1, 2), a: (2, 2), b: (1, 1)}
    main_q = next(x for x in sql if "started>=" in x)
    plan = " ".join(str(tuple(r)) for r in db.conn().execute("EXPLAIN QUERY PLAN " + main_q).fetchall())
    assert "idx_calls_started" in plan and "date(" not in main_q.split("WHERE", 1)[1]


def test_linediag_processes_window_by_window():
    from app import linediag
    rng = np.random.default_rng(3)
    x = (3000 * rng.standard_normal(60 * 8000)).astype("<i2").tobytes()         # 60 s Spur: als float64 am Stueck 3,8 MB
    ref = linediag.analyze(x)
    tracemalloc.start()
    try:
        linediag.analyze(x)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000, peak
    assert ref["dur"] == 60.0 and ref["speech"] > 50


def test_suggestions_for_restricted_users(bridge):
    async def scenario(b):
        me = b.db.save_staff({"name": "Petra Muster", "number": "**610", "away_number": "0171555666", "msn": "0301111"})
        b.db.save_staff({"name": "Peter Kollege", "number": "**620, 0172999888", "away_number": "0160111", "msn": "0302222"})
        b.db.save_contact({"name": "Kunde Geheim", "number": "0221333444"})
        main.app.dependency_overrides[main.user_auth] = lambda: {"id": 5, "username": "petra", "role": "user", "staff_id": me, "disabled": 0}
        try:
            d = (await b.http.get("/api/me/suggest")).json()
            vals = {x["v"] for x in d["caller"] + d["fwd"]}
            assert {"**610", "0171555666", "**620"} <= vals                               # eigene + interne der Kollegen
            assert not vals & {"0172999888", "0160111", "0221333444"}                     # keine fremden externen, kein Telefonbuch
            assert [x["v"] for x in d["called"]] == ["0301111"] and d["phonebook"] is False
            b.db.set_meta("users_phonebook", "1")
            d = (await b.http.get("/api/me/suggest")).json()
            assert "0221333444" in {x["v"] for x in d["caller"]} and d["phonebook"] is True
            assert (await b.http.get("/api/staff")).status_code in (401, 403)             # die Administrator-API bleibt zu
        finally:
            main.app.dependency_overrides.pop(main.user_auth, None)
    run(bridge, scenario)


def test_init_tag_shares_one_list_and_flags_free_text():
    html = open(INDEX, encoding="utf-8").read()
    body = html[html.index("async function initTag("):html.index("function tagPending(")]
    assert 'createElement("div");list.className="cs-list"' not in body and "tagListEl()" in body   # keine neue Liste je Aufruf
    assert "tagFree(" in body and "tg-bad" in body                                         # Freitext wird geprueft, nicht still behalten
    for fn in ("saveStaff", "saveMine", "saveRule", "ruleSave"):
        start = html.index(f"async function {fn}(")
        assert "tagPending()" in html[start:start + 200], fn
    assert 'api("/api/me/suggest")' in html
