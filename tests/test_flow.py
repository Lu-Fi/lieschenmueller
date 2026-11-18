"""Ablauf je Anruf (call_events, flow.py): Ereignisse der wichtigsten Wege und ihre deutschen Texte."""
import time
import uuid

from app import db, flow, main
from tests.conftest import run
from tests.test_transfer import CONNECT_ASK, SCREEN, screen_flow, setup_staff

CALLER = "015112345678"


def texts(cid):
    return [e["text"] for e in flow.list_for(cid)]


def test_describe_and_durations(bridge):
    cid = db.x("INSERT INTO calls(uuid, started, ended) VALUES('x', ?, ?)", (1000.0, 1100.0))
    db.x("INSERT INTO call_events(call_id, ts, kind, data) VALUES(?,?,?,?)", (cid, 1000.0, "ring", '{"targets": "**610+**620", "ring": 25, "rules": ["Abends"]}'))
    db.x("INSERT INTO call_events(call_id, ts, kind, data) VALUES(?,?,?,?)", (cid, 1025.0, "no_answer", "{}"))
    db.x("INSERT INTO call_events(call_id, ts, kind, data) VALUES(?,?,?,?)", (cid, 1026.0, "ai", '{"agent": "Empfang", "rule": "Standard"}'))
    ev = flow.list_for(cid, 1100.0)
    assert [e["text"] for e in ev] == ["klingelte bei **610 + **620 – Regel: Abends, 25 s", "niemand nahm ab",
                                       "Agent Empfang übernahm (Anrufregel Standard)"]
    assert [e["dur"] for e in ev] == [25.0, 1.0, 74.0]
    assert flow.list_for(999999) == []                          # alte Anrufe: keine Ereignisse


def test_flow_ring_no_answer_ai(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Hallo."), ("tool", "end_call")])

    async def scenario(b):
        b.db.x("UPDATE call_rules SET ring_s=20, name='Standard'")
        b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": "0301111", "status": "office"})
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0301111@fritz.box>") == "fdial:20:**610"
        assert await b.internal("fallback", uuid=u) == "ok"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        await b.internal("rec-done", uuid=u)
        assert texts(b.call_row(u)["id"]) == [
            "klingelt bei **610 (Durchstellen: Petra Muster) – Regel: Standard, 20 s".replace("klingelt", "klingelte"),
            "niemand nahm ab", "Agent Assistent übernahm (Anrufregel Standard)", "KI-Gespräch beendet: KI hat aufgelegt", "aufgelegt"]
    run(bridge, scenario)


def test_flow_screen_decline(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("say", "Zeit?"), ("hear", "Nein."), ("tool", "decline")], match=SCREEN)
    bridge.genai.script([("say", "Leider nicht."), ("tool", "end_call")])

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        assert await screen_flow(b, u, None) == "decline"
        assert await b.internal("busy", uuid=u, status="ANSWER") == "ok"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        await b.internal("rec-done", uuid=u)
        t = texts(b.call_row(u)["id"])
        assert t[0].startswith("Agent Assistent übernahm")
        assert t[1:] == ["Rückfrage bei Petra Muster (**610)", "KI-Gespräch beendet: Weiterleitung an **610",
                         "Rückfrage bei Petra Muster → abgelehnt", "Durchstellen fehlgeschlagen (abgelehnt), die KI nimmt wieder an",
                         "KI nahm den Anrufer wieder an (Agent Assistent)", "KI-Gespräch beendet: KI hat aufgelegt", "aufgelegt"]
    run(bridge, scenario)


def test_flow_cost_brake_and_no_agent(bridge):
    async def scenario(b):
        b.db.set_meta("limit_calls_h", "1")
        b.db.x("INSERT INTO calls(uuid,started,ended,caller,called,agent_name,status,direction,cost_usd,tok_out_audio) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (str(uuid.uuid4()), time.time() - 120, time.time() - 60, CALLER, "0309999", "Assistent", "beendet", "in", 0.01, 500))
        u = str(uuid.uuid4())
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0309999@fritz.box>") == "no-agent"
        assert texts(b.call_row(u)["id"])[0].startswith("Kostenbremse: nicht an die KI")
        b.db.x("UPDATE call_rules SET enabled=0")
        u = str(uuid.uuid4())
        assert await b.internal("call", uuid=u, frm="0301234567", to="s", hdr="<sip:0309999@fritz.box>") == "no-agent"
        assert texts(b.call_row(u)["id"]) == ["kein Agent zuständig – nicht angenommen"]
    run(bridge, scenario)


def test_call_detail_api_has_events(bridge):
    async def scenario(b):
        main.app.dependency_overrides[main.user_auth] = lambda: main.PSEUDO_ADMIN
        try:
            cid = db.x("INSERT INTO calls(uuid, started, ended, caller, called, status) VALUES('y', 1.0, 5.0, '1', '2', 'beendet')")
            r = await b.http.get(f"/api/calls/{cid}")
            assert r.status_code == 200 and r.json()["events"] == []
            flow.add(cid, "hangup")
            assert [e["text"] for e in (await b.http.get(f"/api/calls/{cid}")).json()["events"]] == ["aufgelegt"]
            main.remove_call(cid)
            assert not db.q("SELECT 1 FROM call_events WHERE call_id=?", (cid,))
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)
