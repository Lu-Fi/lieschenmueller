"""Review Paket 3: Fernabfrage (wortgenaue Codewoerter, Sperre nach Fehlversuchen), Kostenbremse fuer selbst eingetragene
Nummern, Sichtbarkeit der Anrufe erst ab Zuweisung der Rufnummer."""
import os
import time
import uuid

from app import costguard, main, remote, users
from tests.conftest import run


def test_codes_whole_words_across_fragments():
    st = {"remote_codes": "Rose, Blauer Himmel"}
    seen = set()
    assert not remote.check_codes("Ich esse gern Rosenkohl", seen, st)        # Teilwort zaehlt nicht
    assert not seen
    seen = set()
    assert not remote.check_codes("Die Rose,", seen, st)
    assert remote.check_codes("Die Rose, und ein blau" + "er Himmel.", seen, st)   # Bruchstuecke zusammengesetzt, mehrere Woerter


def test_remote_locked_after_failures(bridge):
    async def scenario(b):
        sid = b.db.save_staff({"name": "Petra Muster", "number": "**610", "away_number": "01701234567", "msn": "", "status": "office"})
        b.db.x("UPDATE staff SET remote_on=1, remote_codes='rose' WHERE id=?", (sid,))
        st = remote.staff_by_caller("01701234567")
        assert st and st["id"] == sid
        assert not remote.note_fail(st) and not remote.note_fail(st)
        assert remote.note_fail(st)                                           # dritter Fehlversuch sperrt
        assert remote.staff_by_caller("01701234567") is None
        remote.note_ok(st)
        assert remote.staff_by_caller("01701234567")
    run(bridge, scenario)


def test_cost_brake_applies_to_self_entered_numbers(bridge):
    async def scenario(b):
        b.db.save_staff({"name": "Petra Muster", "number": "030555666", "away_number": "01701234567", "msn": "", "status": "office"})
        assert not costguard.exempt("01701234567")                            # "ausser Haus" traegt der Benutzer selbst ein
        assert not costguard.exempt("030555666")
        assert costguard.exempt("**610")
    run(bridge, scenario)


def test_calls_visible_only_after_assignment(bridge):
    async def scenario(b):
        sid = b.db.save_staff({"name": "Neu", "number": "**611", "msn": "", "status": "office"})
        old = b.db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction) VALUES(?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), time.time() - 86400, "030123", "0309999", "x", "beendet", "in"))
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        r = await b.http.put("/api/numbers/0309999", json={"staff_ids": [sid]})
        assert r.status_code == 200, r.text
        new = b.db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction) VALUES(?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), time.time() + 1, "030123", "0309999", "x", "beendet", "in"))
        u = {"id": 99, "role": "user", "staff_id": sid}
        where, args = users.sql_scope(u)
        assert [r["id"] for r in b.db.q(f"SELECT id FROM calls WHERE {where}", args)] == [new]
        rows = {c["id"]: c for c in b.db.q("SELECT * FROM calls")}
        assert users.level_for(u, rows[new]) == "full" and users.level_for(u, rows[old]) is None
        st = b.db.q("SELECT * FROM staff WHERE id=?", (sid,))[0]
        assert remote.get_call(dict(st), old) is None and remote.get_call(dict(st), new)
        await b.http.put("/api/numbers/0309999", json={"staff_ids": []})
        main.app.dependency_overrides.clear()
        assert not b.db.get_meta(f"msn_since:{sid}:0309999", "")
    run(bridge, scenario)


def test_shared_call_cannot_be_reshared_and_away_numbers_hidden(bridge):
    async def scenario(b):
        from app import users as U
        owner_sid = b.db.save_staff({"name": "Inhaber", "number": "**611", "away_number": "01701112223", "msn": "0309999", "status": "office"})
        other_sid = b.db.save_staff({"name": "Andere", "number": "**612", "msn": "", "status": "office"})
        owner = U.create("inhaber", "Passwort-1234", "user", owner_sid, check=False)
        other = U.create("andere", "Passwort-1234", "user", other_sid, check=False)
        third = U.create("dritte", "Passwort-1234", "user", None, check=False)
        cid = b.db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction) VALUES(?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), time.time(), "030123", "0309999", "x", "beendet", "in"))
        uo = b.db.q("SELECT * FROM users WHERE id=?", (owner,))[0]
        ua = b.db.q("SELECT * FROM users WHERE id=?", (other,))[0]
        try:
            main.app.dependency_overrides[main.user_auth] = lambda: dict(uo)
            assert (await b.http.put(f"/api/calls/{cid}/shares", json={"user_ids": [other]})).status_code == 200
            main.app.dependency_overrides[main.user_auth] = lambda: dict(ua)
            r = await b.http.put(f"/api/calls/{cid}/shares", json={"user_ids": [third]})
            assert r.status_code == 403                                       # nur freigegeben bekommen: nicht weitergeben
            live = (await b.http.get("/api/live")).json()
            st = {s["name"]: s for s in live["staff"]}
            assert st["Inhaber"]["away"] == f"staff:{owner_sid}:away" and st["Inhaber"]["away_hidden"]
            assert main.resolve_target(st["Inhaber"]["away"]) == "01701112223"
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)


def test_secret_files_not_world_readable(tmp_path, monkeypatch):
    from app import fritzconf
    p = tmp_path / "fritz.conf"
    fritzconf.write(str(p), "u", "geheim", "192.168.178.1")
    assert os.stat(p).st_mode & 0o007 == 0 or os.getuid() != 0             # als root: Gruppe Asterisk, sonst niemand
    monkeypatch.setattr(fritzconf, "ASTERISK_GID", 999999)
    monkeypatch.setattr(os, "chown", lambda *a: (_ for _ in ()).throw(PermissionError()))
    fritzconf.write(str(p), "u", "geheim", "192.168.178.1")
    assert os.stat(p).st_mode & 0o777 == 0o644                              # ohne Recht: wie bisher lesbar (Asterisk muss lesen)
