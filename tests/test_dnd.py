"""Status "nicht stoeren": wie "nicht erreichbar", aber Anrufer aus der Ausnahmeliste werden weiter verbunden
(beim Klingeln und wenn die KI verbindet); die KI nennt anderen Anrufern keinen Grund."""
import uuid

from app import main
from tests.conftest import run
from tests.test_routing import HDR, call, ring, staff

SON = "015100000001"


def test_dnd_rings_only_for_exceptions(bridge):
    async def scenario(b):
        ring(b.db)
        dep = staff(b.db, "Clemens Vertreter", "**630", msn="")
        sid = staff(b.db, "Petra Muster", "**610", status="dnd", deputy_id=dep)
        b.db.save_staff(dict(b.db.q("SELECT * FROM staff WHERE id=?", (sid,))[0], dnd_allow="0151 000 00001; +49171x5550000, 12"), sid)
        assert b.db.q("SELECT dnd_allow FROM staff WHERE id=?", (sid,))[0]["dnd_allow"] == "015100000001,+491715550000"
        assert await call(b, frm="+49" + SON[1:])[1] == "fdial:20:**610"        # Ausnahme (jede Schreibweise): Buero-Telefon
        u, r = call(b)
        assert await r == "fdial:20:**630"                                     # alle anderen: Vertretung
        assert "Vertretung für Petra Muster" in b.call_row(u)["agent_name"]
        b.db.x("UPDATE staff SET status='office' WHERE id=?", (dep,))
        b.db.x("UPDATE staff SET deputy_id=NULL WHERE id=?", (sid,))
        assert await call(b)[1] == "ok"         # ohne Vertretung: die KI nimmt sofort an (kein ring_all, sonst klingelte Petras Telefon)
    run(bridge, scenario)


def test_dnd_connect_employee_and_prompt(bridge):
    u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
    steps = [("say", "Guten Tag."), ("hear", "Petra bitte."), ("tool", "connect_employee", {"name": "Petra"}), ("say", "Moment.")]
    bridge.genai.script(steps)
    bridge.genai.script(steps + [("tool", "end_call")])

    async def scenario(b):
        sid = b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": "", "status": "dnd", "dnd_allow": SON})
        assert await b.internal("call", uuid=u1, frm=SON, to="s", hdr="<sip:0309999@fritz.box>", chan="PJSIP/fritz-1") == "ok"
        sock = await b.audiosocket(u1)
        await b.wait_ended(sock)
        s1 = b.genai.sessions[0]
        assert s1.tool_results[0][1]["ok"] is True                              # der Sohn wird verbunden
        assert "- Petra Muster: erreichbar" in s1.prompt
        assert await b.internal("after", uuid=u1) == "dial:**610:default"
        assert await b.internal("call", uuid=u2, frm="030555444", to="s", hdr="<sip:0309999@fritz.box>", chan="PJSIP/fritz-2") == "ok"
        sock = await b.audiosocket(u2)
        await b.wait_ended(sock)
        s2 = b.genai.sessions[1]
        assert "nicht erreichbar" in s2.tool_results[0][1]["fehler"]             # andere nicht
        assert "- Petra Muster: nicht erreichbar" in s2.prompt and "stören" not in s2.prompt        # "nicht stoeren" bleibt intern
        assert sid
    run(bridge, scenario)


def test_me_staff_saves_dnd(bridge):
    async def scenario(b):
        sid = b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": "", "status": "office"})
        b.db.x("INSERT INTO users(username,pw_hash,role,staff_id) VALUES('petra','x','user',?)", (sid,))
        uid = b.db.q("SELECT id FROM users WHERE username='petra'")[0]["id"]
        main.app.dependency_overrides[main.user_auth] = lambda: {"id": uid, "username": "petra", "role": "user", "staff_id": sid, "disabled": 0}
        r = await b.http.put("/api/me/staff", json={"status": "dnd", "dnd_allow": "0151 00000001"})
        main.app.dependency_overrides.pop(main.user_auth, None)
        assert r.status_code == 200, r.text
        row = b.db.q("SELECT status, dnd_allow FROM staff WHERE id=?", (sid,))[0]
        assert row == {"status": "dnd", "dnd_allow": "015100000001"}
    run(bridge, scenario)


def test_dnd_allow_wildcard_prefix(bridge):
    """Ausnahme mit * am Ende: alle Nummern mit diesem Anfang (z. B. alle Durchwahlen einer Firma), jede Schreibweise."""
    db = bridge.db
    assert db.clean_dnd_allow("0221555*, 02*, 0221555*") == "0221555*"          # zu kurz (<4 Ziffern) und Doppelte fallen weg
    s = {"dnd_allow": "0221555*,015100000001"}
    assert db.dnd_allows(s, "022155512")
    assert db.dnd_allows(s, "+4922155599")
    assert db.dnd_allows(s, "004922155501")
    assert not db.dnd_allows(s, "022155610")
    assert not db.dnd_allows(s, "030221555")                     # nur der Anfang zaehlt
    assert db.dnd_allows(s, "+4915100000001") and not db.dnd_allows(s, "")
