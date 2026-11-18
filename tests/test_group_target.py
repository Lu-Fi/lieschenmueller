"""Sammelziel (staff.kind='group', z. B. Zentrale): nur Nummern, kein Status, keine Anwesenheit, kein Konto; nicht als Kollege in der
Rueckfrage, aber als Vertretung und als Ziel fuer pass_on/connect nutzbar. Ein alter Eintrag "Zentrale" (Person) arbeitet weiter."""
import uuid

from app import call as call_mod, main, presence
from tests.conftest import run
from tests.test_routing import call, ring, staff


def admin(fn):
    async def wrapped(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            await fn(b)
        finally:
            main.app.dependency_overrides.pop(main.auth, None)
    return wrapped


def test_legacy_zentrale_row_keeps_working(bridge):
    async def scenario(b):
        ring(b.db)
        z = staff(b.db, "Zentrale", "**600", msn="")                 # Altbestand: Person "Zentrale"
        staff(b.db, "Petra Muster", "**610", status="off", deputy_id=z)
        assert b.db.q("SELECT kind FROM staff WHERE id=?", (z,))[0]["kind"] == "person"
        assert await call(b)[1] == "fdial:20:**600"
    run(bridge, scenario)


def test_group_has_no_status_presence_or_account(bridge):
    @admin
    async def scenario(b):
        r = await b.http.post("/api/staff", json={"kind": "group", "name": "Zentrale", "number": "**600, **601", "status": "dnd",
                                                  "note": "geheim", "presence_mac": "AA:BB:CC:DD:EE:01", "dnd_allow": "0171"})
        assert r.status_code == 200, r.text
        z = r.json()["id"]
        row = b.db.q("SELECT * FROM staff WHERE id=?", (z,))[0]
        assert (row["kind"], row["status"], row["note"], row["presence_mac"], row["dnd_allow"], row["number"]) == \
            ("group", "office", "", "", "", "**600, **601")
        assert (await b.http.put(f"/api/staff/{z}/status", json={"status": "off"})).status_code == 400
        assert (await b.http.post("/api/users", json={"username": "zentrale", "password": "x" * 12, "staff_id": z})).status_code == 400
        uid = b.db.x("INSERT INTO users(username,pw_hash,role) VALUES('frei','x','user')")
        assert (await b.http.put(f"/api/users/{uid}", json={"role": "user", "staff_id": z})).status_code == 400
        # Altbestand mit Konto: erst das Konto loesen
        p = b.db.save_staff({"name": "Empfang", "number": "**602", "msn": ""})
        b.db.x("UPDATE users SET staff_id=? WHERE id=?", (p, uid))
        r = await b.http.put(f"/api/staff/{p}", json={"kind": "group", "name": "Empfang", "number": "**602"})
        assert r.status_code == 400 and "Konto" in r.json()["detail"]
        b.db.x("UPDATE users SET staff_id=NULL WHERE id=?", (uid,))
        assert (await b.http.put(f"/api/staff/{p}", json={"kind": "group", "name": "Empfang", "number": "**602"})).status_code == 200
        # Speichern ohne "kind" behaelt die Art
        b.db.save_staff({"name": "Empfang", "number": "**603"}, p)
        assert b.db.q("SELECT kind FROM staff WHERE id=?", (p,))[0]["kind"] == "group"
        # Anwesenheit beruecksichtigt Sammelziele nie
        b.db.x("UPDATE staff SET presence_on=1, presence_mac='AA:BB:CC:DD:EE:01', status='off' WHERE id=?", (z,))
        assert presence.tick(lambda m: True, 1000.0) == []
    run(bridge, scenario)


def test_group_as_deputy_and_reachable_without_status(bridge):
    async def scenario(b):
        ring(b.db)
        z = b.db.save_staff({"kind": "group", "name": "Zentrale", "number": "**600, **601", "msn": ""})
        b.db.x("UPDATE staff SET status='off' WHERE id=?", (z,))             # alter Wert in der Datenbank zaehlt nicht
        staff(b.db, "Petra Muster", "**610", status="off", deputy_id=z)
        u, r = call(b)
        assert await r == "fdial:20:**600+**601"
        assert "Zentrale (Vertretung für Petra Muster)" in b.call_row(u)["agent_name"]
        zr = next(s for s in b.db.list_staff() if s["id"] == z)
        assert zr["status"] == "office" and b.db.resolve(zr, "030555")["number"] == "**600, **601"
    run(bridge, scenario)


def test_screening_lists_group_separately_and_prompt_marks_it(bridge):
    bridge.db.save_staff({"kind": "group", "name": "Zentrale", "number": "**600", "msn": ""})
    bridge.db.save_staff({"name": "Clemens Kollege", "number": "**620", "msn": ""})
    bridge.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": ""})
    c = call_mod.Call.__new__(call_mod.Call)
    c.meta = {"screen": {"who": "Petra Muster"}}
    txt = c._colleagues()
    assert "Moegliche Kollegen: Clemens Kollege." in txt and "Zentrale" not in txt.split("Sammelziele")[0]
    assert "Sammelziele (keine Personen): Zentrale" in txt


def test_ai_connects_to_group_target(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Guten Tag."), ("hear", "Die Zentrale bitte."), ("tool", "connect_employee", {"name": "Zentrale"}),
                         ("say", "Moment.")])

    async def scenario(b):
        b.db.save_staff({"kind": "group", "name": "Zentrale", "number": "**600", "msn": ""})
        assert await b.internal("call", uuid=u, frm="030555444", to="s", hdr="<sip:0309999@fritz.box>", chan="PJSIP/fritz-1") == "ok"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        s1 = b.genai.sessions[0]
        assert s1.tool_results[0][1]["ok"] is True and "- Zentrale [Sammelziel]: erreichbar" in s1.prompt
        assert await b.internal("after", uuid=u) == "dial:**600:default"
    run(bridge, scenario)
