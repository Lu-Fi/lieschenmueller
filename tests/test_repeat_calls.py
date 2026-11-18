"""Anrufregel "mehrfach angerufen": fruehere Anrufe derselben Nummer zaehlen, Ausgang je Anruf, Platzhalter {name}/{anzahl}/{minuten}."""
import json
import time
import uuid

from app import agents, main
from app.call import Call, split_name
from tests.conftest import run

CALLER = "01701234567"


def old_call(db, status, minutes_ago=2, caller=CALLER, notes=None):
    return db.x("INSERT INTO calls(uuid,started,ended,caller,called,agent_name,status,direction,notes) VALUES(?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), time.time() - minutes_ago * 60, time.time(), caller, "0301111", "x", status, "in",
                 json.dumps(notes or {})))


def repeat_rule(db, greeting="", n=2, minutes=10):
    aid = db.save_agent({"name": "Dringend", "greeting": greeting})
    db.x("INSERT INTO call_rules(name,priority,agent_id,ring_s,repeat_n,repeat_min) VALUES('Mehrfach',1,?,0,?,?)", (aid, n, minutes))
    return aid


def test_recent_calls_outcomes_and_window(bridge):
    async def scenario(b):
        old_call(b.db, "unbeantwortet", 8)
        old_call(b.db, "angenommen", 6, caller="+491701234567")              # gleiche Nummer mit Landesvorwahl
        old_call(b.db, "beendet", 4, notes={"Nachricht für Frau Meier": "Rueckruf"})
        old_call(b.db, "beendet", 3)
        old_call(b.db, "unbeantwortet", 30)                                   # ausserhalb des Zeitraums
        old_call(b.db, "unbeantwortet", 1, caller="030999888")                # andere Nummer
        hist = b.db.recent_calls_from(CALLER, 10)
        assert [h["outcome"] for h in hist] == ["aufgelegt, ohne mit jemandem zu sprechen", "mit jemandem gesprochen",
                                               "Nachricht hinterlassen", "mit der KI gesprochen, keine Nachricht"]
        assert b.db.recent_calls_from("", 10) == []                           # unterdrueckte Nummer zaehlt nie
    run(bridge, scenario)


def test_rule_applies_only_after_enough_calls(bridge):
    async def scenario(b):
        repeat_rule(b.db)
        old_call(b.db, "unbeantwortet")
        assert agents.pick_agent(CALLER, ["0301111"])["name"] == "Assistent"
        u = str(uuid.uuid4())
        b.db.x("INSERT INTO calls(uuid,started,caller,called,status,direction) VALUES(?,?,?,?,?,?)", (u, time.time(), CALLER, "0301111", "aktiv", "in"))
        assert agents.pick_agent(CALLER, ["0301111"], uuid=u)["name"] == "Assistent"     # dieser Anruf zaehlt nicht mit
        old_call(b.db, "beendet", 1, notes={"Nachricht für Büro": "x"})
        a = agents.pick_agent(CALLER, ["0301111"], uuid=u)
        assert a["name"] == "Dringend" and a["repeat"]["n"] == 2 and a["repeat"]["min"] == 10
    run(bridge, scenario)


def test_greeting_placeholders_and_history_in_prompt(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Hallo."), ("tool", "end_call")])

    async def scenario(b):
        b.db.x("INSERT INTO contacts(name,number,salutation) VALUES('Lorenz, Anja',?,'Frau')", (CALLER,))
        repeat_rule(b.db, "Sage: Hallo Frau {nachname}, Sie haben in den letzten {minuten} Minuten schon {anzahl}-mal angerufen.")
        old_call(b.db, "unbeantwortet", 5)
        old_call(b.db, "beendet", 2, notes={"Nachricht für Büro": "x"})
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0301111@fritz.box>", chan="PJSIP/fritz-1") == "ok"
        sock = await b.audiosocket(u)
        await sock.audio(10)
        await b.wait_ended(sock)
        s = b.genai.sessions[0]
        assert "Hallo Frau Lorenz, Sie haben in den letzten 10 Minuten schon 2-mal angerufen." in s.texts[0]
        assert "WIEDERHOLTER ANRUF" in s.prompt and "Nachricht hinterlassen" in s.prompt and "aufgelegt, ohne" in s.prompt
        assert "laut Kontaktliste Lorenz, Anja" in s.prompt and "Anrede laut Kontaktliste: Frau" in s.prompt
    run(bridge, scenario)


def test_unknown_name_drops_salutation():
    c = Call.__new__(Call)
    c.meta, c.agent = {"from": "030777666", "uuid": "x"}, {"repeat": {"n": 3, "min": 15, "calls": []}}
    assert c.fill("Hallo Herr {name}, schon {anzahl}-mal in {minuten} Minuten. {foo}") == "Hallo , schon 3-mal in 15 Minuten. {foo}"
    assert split_name("Lorenz, Anja") == ("Anja", "Lorenz") and split_name("Anja Lorenz") == ("Anja", "Lorenz")
    assert split_name("Lorenz") == ("", "Lorenz")
    assert split_name("Feider; Thomas") == ("Thomas", "Feider") and split_name("Beil;Thomas") == ("Thomas", "Beil")


def test_rule_api_validates_repeat(bridge):
    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        aid = b.db.list_agents()[0]["id"]
        r = await b.http.post("/api/rules/call", json={"name": "x", "agent_id": aid, "repeat_n": 3, "repeat_min": 0})
        assert r.status_code == 400
        r = await b.http.post("/api/rules/call", json={"name": "x", "agent_id": aid, "repeat_n": 3, "repeat_min": 10})
        assert r.status_code == 200
        row = b.db.q("SELECT repeat_n, repeat_min FROM call_rules WHERE id=?", (r.json()["id"],))[0]
        assert row == {"repeat_n": 3, "repeat_min": 10}
        r = await b.http.post("/api/rules/call", json={"name": "y", "agent_id": aid, "repeat_n": 0, "repeat_min": 10})
        assert b.db.q("SELECT repeat_min FROM call_rules WHERE id=?", (r.json()["id"],))[0]["repeat_min"] == 0
        main.app.dependency_overrides.pop(main.auth, None)
    run(bridge, scenario)


def test_count_only_hangups(bridge):
    async def scenario(b):
        aid = repeat_rule(b.db, n=2)
        b.db.x("UPDATE call_rules SET repeat_kind='hangup' WHERE agent_id=?", (aid,))
        old_call(b.db, "unbeantwortet", 5)
        old_call(b.db, "beendet", 4, notes={"Nachricht für Büro": "x"})
        old_call(b.db, "angenommen", 3)
        assert agents.pick_agent(CALLER, ["0301111"])["name"] == "Assistent"       # nur 1 aufgelegter Anruf
        old_call(b.db, "abgebrochen", 1)
        a = agents.pick_agent(CALLER, ["0301111"])
        assert a["name"] == "Dringend" and a["repeat"]["n"] == 2 and len(a["repeat"]["calls"]) == 4
        b.db.x("UPDATE call_rules SET repeat_kind='nomsg', repeat_n=4 WHERE agent_id=?", (aid,))
        assert agents.pick_agent(CALLER, ["0301111"])["name"] == "Assistent"       # 3 ohne Nachricht
    run(bridge, scenario)


def test_salutation_from_contact_never_guessed(bridge):
    async def scenario(b):
        cid = b.db.save_contact({"name": "Anja Lorenz", "number": CALLER})
        c = Call.__new__(Call)
        c.meta, c.agent = {"from": CALLER, "uuid": "x"}, {}
        assert c.fill("Hallo Herr {nachname}, {anrede}") == "Hallo Lorenz, Anja Lorenz"      # keine Anrede hinterlegt: kein Raten
        b.db.x("UPDATE contacts SET source='fritz' WHERE id=?", (cid,))
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        assert (await b.http.put(f"/api/contacts/{cid}/salutation", json={"salutation": "Frau"})).status_code == 200
        assert (await b.http.put(f"/api/contacts/{cid}/salutation", json={"salutation": "Dr"})).status_code == 400
        main.app.dependency_overrides.pop(main.auth, None)
        assert c.fill("Hallo Herr {nachname}, {anrede}") == "Hallo Frau Lorenz, Frau Lorenz"   # auch beim Fritzbox-Kontakt
    run(bridge, scenario)
