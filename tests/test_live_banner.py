"""Live-Banner (Animationen in der Oberflaeche): das Durchstellen durch die KI ist sofort als Ereignis und im Stand
(live_snapshot "handover") sichtbar, eine aufgenommene Nachricht als "msg"; Ereignisse fuer den SSE-Stand."""
import time
import uuid
from types import SimpleNamespace

from app import main, plugins
from app.events import hub
from tests.conftest import run
from tests.test_transfer import CONNECT, setup_staff, start_ai


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_ai_transfer_publishes_call_event_before_end(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT)

    async def scenario(b):
        setup_staff(b.db)
        q = hub.subscribe()
        try:
            await start_ai(b, u)
        finally:
            hub.unsubscribe(q)
        cid = b.call_row(u)["id"]
        evs = [e for e in drain(q) if e.get("call_id") == cid]
        kinds = [(e["type"], e.get("status")) for e in evs if e["type"] in ("call", "end")]
        assert ("call", "durchgestellt") in kinds
        assert kinds.index(("call", "durchgestellt")) < kinds.index(("end", None))     # Uebergabe vor dem Ende der KI-Sitzung
    run(bridge, scenario)


def test_note_message_counts_as_message_left(bridge, monkeypatch):
    u = str(uuid.uuid4())
    bridge.genai.script([
        ("say", "Guten Tag, wie kann ich helfen?"),
        ("hear", "Ich moechte Frau Meier eine Nachricht hinterlassen."),
        ("tool", "note_message", {"empfaenger": "Frau Meier", "nachricht": "Bitte zurueckrufen"}),
        ("say", "Ich richte es aus. Auf Wiederhoeren."),
        ("tool", "end_call"),
    ])
    seen = []
    orig = plugins.emit

    async def emit(event, *args):
        if event == "call_end":
            seen.append(args[0])
        return await orig(event, *args)
    monkeypatch.setattr(plugins, "emit", emit)

    async def scenario(b):
        q = hub.subscribe()
        try:
            assert await b.internal("call", uuid=u, frm="030123456", to="s", hdr="<sip:99613@fritz.box>", chan="PJSIP/fritz-1") == "ok"
            sock = await b.audiosocket(u)
            await b.wait_ended(sock)
        finally:
            hub.unsubscribe(q)
        assert seen and seen[0].messages_left() == 1
        assert any(e["type"] == "note" for e in drain(q))        # loest im SSE-Stand ein neues Bild aus
    run(bridge, scenario)


def test_live_snapshot_handover_and_msg(bridge, monkeypatch):
    async def scenario(b):
        cid = b.db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction) VALUES(?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), time.time(), "030123456", "0309999", "Empfang", "aktiv", "in"))
        c = SimpleNamespace(alive=True, meta={"call_id": cid, "from": "030123456", "direction": "in"}, agent={"name": "Empfang"},
                            after=None, notes={})
        c.messages_left = lambda: main.call_mod.Call.messages_left(c)
        monkeypatch.setattr(main, "active_calls", lambda: [c])
        row = main.live_snapshot(main.PSEUDO_ADMIN)["calls"][0]
        assert row["id"] == cid and row["handover"] is False and row["msg"] == 0
        c.notes = {"Nachricht für Frau Meier": "Bitte zurueckrufen", "Anliegen": "Rechnung"}
        c.after = "dial:**610:default"
        row = main.live_snapshot(main.PSEUDO_ADMIN)["calls"][0]
        assert row["handover"] is True and row["msg"] == 1
    run(bridge, scenario)
