"""Sieht auch die Anrufe von (see_staff): z. B. Petra sieht die Anrufe auf die Nummer der Zentrale."""
import time
import uuid

from app import main, users
from tests.conftest import run


def _call(b, called, t):
    return b.db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction) VALUES(?,?,?,?,?,?,?)",
                  (str(uuid.uuid4()), t, "030123", called, "x", "beendet", "in"))


def test_user_sees_calls_of_central_number(bridge):
    async def scenario(b):
        z = b.db.save_staff({"name": "Zentrale", "kind": "group", "number": "**610"})
        sab = b.db.save_staff({"name": "Petra", "number": "**612", "status": "office"})
        other = b.db.save_staff({"name": "Andere", "number": "**613", "status": "office"})
        old = _call(b, "5550270", time.time() - 86400)
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            assert (await b.http.put("/api/numbers/5550270", json={"staff_ids": [z]})).status_code == 200
            st = next(s for s in b.db.list_staff() if s["id"] == sab)
            r = await b.http.put(f"/api/staff/{sab}", json=dict(st, see_staff=f"{z},{sab},9999,{z}"))
            assert r.status_code == 200, r.text
        finally:
            main.app.dependency_overrides.clear()
        assert b.db.q("SELECT see_staff FROM staff WHERE id=?", (sab,))[0]["see_staff"] == str(z)   # ohne sich selbst, Unbekannte, Doppelte
        new = _call(b, "5550270", time.time() + 1)
        us = {"id": 98, "role": "user", "staff_id": sab}
        uo = {"id": 97, "role": "user", "staff_id": other}
        where, args = users.sql_scope(us)
        assert [r["id"] for r in b.db.q(f"SELECT id FROM calls WHERE {where}", args)] == [new]    # erst ab der Zuweisung an die Zentrale
        rows = {c["id"]: c for c in b.db.q("SELECT * FROM calls")}
        assert users.level_for(us, rows[new]) == "full" and users.level_for(us, rows[old]) is None
        assert users.may_share(us, rows[new])
        assert users.level_for(uo, rows[new]) is None
        # Klingeln unveraendert: die Nummer gehoert weiter nur der Zentrale
        assert [s["name"] for s in b.db.staffs_for_number(["5550270"])] == ["Zentrale"]
    run(bridge, scenario)


def test_own_edit_keeps_see_staff_and_group_clears_it(bridge):
    async def scenario(b):
        z = b.db.save_staff({"name": "Zentrale", "kind": "group", "number": "**610"})
        sab = b.db.save_staff({"name": "Petra", "number": "**612", "status": "office", "see_staff": str(z)})
        uid = users.create("petra", "Passwort-1234", "user", sab, check=False)
        u = b.db.q("SELECT * FROM users WHERE id=?", (uid,))[0]
        main.app.dependency_overrides[main.user_auth] = lambda: dict(u)
        try:
            r = await b.http.put("/api/me/staff", json={"status": "away", "see_staff": ""})       # selbst nicht aenderbar
            assert r.status_code == 200, r.text
        finally:
            main.app.dependency_overrides.clear()
        assert b.db.q("SELECT see_staff FROM staff WHERE id=?", (sab,))[0]["see_staff"] == str(z)
        g = b.db.save_staff({"name": "Gruppe", "kind": "group", "number": "**611", "see_staff": str(z)})
        assert b.db.q("SELECT see_staff FROM staff WHERE id=?", (g,))[0]["see_staff"] == ""
    run(bridge, scenario)
