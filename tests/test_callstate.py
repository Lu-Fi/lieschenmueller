"""Zustand je Anruf (callstate.py): Phasen, Ablaufzeiten an einer Stelle, nach dem Auflegen bleibt nichts uebrig."""
import time
import uuid

from app import main
from app.callstate import CALLS, TTL, Phase, Registry
from tests.conftest import run
from tests.test_transfer import CONNECT_ASK, SCREEN, setup_staff

CALLER = "015112345678"


def test_registry_put_take_gc():
    r = Registry()
    r.put("u1", "after", ("dial:**610", time.time()))
    assert r.field("u1", "after")[0] == "dial:**610" and len(r) == 1
    assert r.take("u1", "after")[0] == "dial:**610"
    assert r.get("u1") is None                                  # leerer Eintrag verschwindet
    assert r.take("u1", "after", ("hangup", 0)) == ("hangup", 0)


def test_expire_uses_ttls_and_reports():
    r = Registry()
    now = time.time()
    r.put("u", "after", ("dial:**610", now - TTL["after"] - 1))
    r.put("u", "fallback", {"t": now})
    r.put("v", "pending", {"created": now - TTL["pending"] - 1, "call_id": 7})
    r.put("w", "human", {"id": 1})
    gone = r.expire(now)
    assert sorted((u, n) for u, n, _ in gone) == [("u", "after"), ("v", "pending")]
    assert r.field("u", "fallback") and r.get("v") is None
    assert r.expire(now + TTL["human"] + 1)                     # auch Anzeige-Eintraege laufen ab
    assert len(r) == 0


def test_finish_clears_everything_but_running_session():
    r = Registry()
    for f, v in (("after", ("sdial:**610:25::", time.time())), ("retarget", {"t": time.time(), "target": "**610"}),
                 ("screen", {"t": time.time()}), ("defer_post", True), ("chan", {"t": time.time(), "chan": "x"}), ("active", object())):
        r.put("u", f, v)
    r.finish("u")
    rec = r.get("u")
    assert rec.phase == Phase.ENDED and rec.active is not None
    assert all(getattr(rec, f) is None for f in TTL)
    r.take("u", "active")
    assert r.get("u") is None


def test_unexpected_transition_only_logged(caplog):
    r = Registry()
    r.phase("u", Phase.ENDED)
    r.phase("u", Phase.AI)                                      # nach dem Auflegen unerwartet: nur protokolliert
    assert r.get("u").phase == Phase.AI
    assert "unerwarteter Phasenwechsel" in caplog.text


def test_janitor_marks_unreached_pending(bridge):
    async def scenario(b):
        u = str(uuid.uuid4())
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0309999@fritz.box>") == "ok"
        cid = b.call_row(u)["id"]
        await main.janitor_tick(5, now=time.time() + TTL["pending"] + 1)
        assert b.call_row(u)["status"] == "abgebrochen"
        assert CALLS.get(u) is None or not CALLS.field(u, "pending")
        assert cid
    run(bridge, scenario)


def test_screen_decline_phases_and_nothing_left(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("say", "Hallo, Zeit?"), ("hear", "Nein."), ("tool", "decline")], match=SCREEN)
    bridge.genai.script([("say", "Leider nicht erreichbar."), ("tool", "end_call")])

    async def scenario(b):
        from tests.test_transfer import screen_flow
        setup_staff(b.db, ask_before=True)
        assert await screen_flow(b, u, None) == "decline"
        rec = CALLS.get(u)
        assert rec.phase == Phase.SCREENING and rec.screen
        assert await b.internal("busy", uuid=u, status="ANSWER") == "ok"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        assert await b.internal("after", uuid=u) == "hangup"
        phases = [p.value for _, p in CALLS.get(u).history]
        assert phases == ["ki", "rueckfrage", "ki"]
        assert await b.internal("rec-done", uuid=u) == "ok"
        assert len(CALLS) == 0                                  # nichts bleibt uebrig, auch nicht die Rueckfrage-Sitzung
    run(bridge, scenario)


def test_ring_answered_nothing_left(bridge):
    async def scenario(b):
        b.db.x("UPDATE call_rules SET ring_s=20")
        b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": "0301111", "status": "office"})
        u = str(uuid.uuid4())
        await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0301111@fritz.box>", chan="PJSIP/fritz-1")
        assert CALLS.get(u).phase == Phase.RINGING
        await b.internal("answered", uuid=u)
        assert CALLS.get(u).phase == Phase.CONNECTED
        await b.internal("rec-done", uuid=u)
        assert len(CALLS) == 0
    run(bridge, scenario)


def test_old_after_never_survives_new_session(bridge):
    """Fehlerklasse 'altes AFTER': ein Rest vom vorigen Durchstellen darf eine neue KI-Sitzung nicht beeinflussen."""
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Hallo."), ("tool", "end_call")])

    async def scenario(b):
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0309999@fritz.box>") == "ok"
        CALLS.put(u, "after", ("dial:**610", time.time()))      # Rest eines frueheren Abschnitts
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        assert await b.internal("after", uuid=u) == "hangup"
    run(bridge, scenario)
