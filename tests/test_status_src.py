"""Herkunft des Status (staff.status_src) und Datenschutz der WLAN-Anwesenheit: die Anwesenheit nimmt nur zurueck, was sie selbst
gesetzt hat; einschalten kann sie nur die Person selbst; kein Verlauf (Name + Zeit) in Protokoll oder Datenbank."""
import logging

from app import main, presence
from tests.conftest import run

MAC = "AA:BB:CC:DD:EE:01"


def st(db, sid):
    return db.q("SELECT status, status_src FROM staff WHERE id=?", (sid,))[0]


def admin(fn):
    """Szenario mit Administrator-Anmeldung (main.auth)."""
    async def wrapped(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            await fn(b)
        finally:
            main.app.dependency_overrides.pop(main.auth, None)
            main.app.dependency_overrides.pop(main.user_auth, None)
    return wrapped


def as_user(b, sid, role="user"):
    b.db.x("INSERT INTO users(username,pw_hash,role,staff_id) VALUES(?,?,?,?)", (f"u{sid}", "x", role, sid))
    uid = b.db.q("SELECT id FROM users WHERE staff_id=?", (sid,))[0]["id"]
    main.app.dependency_overrides[main.user_auth] = lambda: {"id": uid, "username": f"u{sid}", "role": role, "staff_id": sid, "disabled": 0}


def test_presence_reverts_only_its_own_status(bridge):
    db = bridge.db
    presence._seen.clear()
    sid = db.save_staff({"name": "Petra", "number": "**610", "msn": "", "status": "off", "presence_mac": MAC})
    db.x("UPDATE staff SET presence_on=1 WHERE id=?", (sid,))
    assert st(db, sid) == {"status": "off", "status_src": "manual"}
    t = 1_000_000.0
    presence.tick(lambda m: True, t)
    assert st(db, sid) == {"status": "dnd", "status_src": "presence"}
    # die Person stellt danach selbst "nicht stoeren" ein: das bleibt, auch wenn das Handy geht
    db.save_staff(dict(db.q("SELECT * FROM staff WHERE id=?", (sid,))[0], status="off"), sid)
    db.save_staff(dict(db.q("SELECT * FROM staff WHERE id=?", (sid,))[0], status="dnd"), sid)
    assert st(db, sid)["status_src"] == "manual"
    assert presence.tick(lambda m: False, t + 5000) == [] and st(db, sid)["status"] == "dnd"
    # Speichern anderer Felder (gleicher Status) laesst die Herkunft stehen
    db.x("UPDATE staff SET status_src='presence' WHERE id=?", (sid,))
    db.save_staff(dict(db.q("SELECT * FROM staff WHERE id=?", (sid,))[0], note="x"), sid)
    assert st(db, sid)["status_src"] == "presence"


def test_presence_after_restart_waits_grace_and_keeps_no_history(bridge, caplog):
    db = bridge.db
    presence._seen.clear()                     # nach einem Neustart weiss die Bridge nicht, wann das Geraet zuletzt da war
    sid = db.save_staff({"name": "Petra Geheim", "number": "**610", "msn": "", "status": "dnd", "presence_mac": MAC})
    db.x("UPDATE staff SET presence_on=1, status_src='presence' WHERE id=?", (sid,))
    t = 2_000_000.0
    with caplog.at_level(logging.DEBUG):
        assert presence.tick(lambda m: False, t) == []                         # ab jetzt die Wartezeit
        assert presence.tick(lambda m: False, t + presence.GRACE_S) == [("Petra Geheim", "dnd", "off")]
        presence.tick(lambda m: True, t + presence.GRACE_S + 60)
    assert st(db, sid) == {"status": "dnd", "status_src": "presence"}
    assert "Petra Geheim" not in caplog.text                                  # kein Name im Protokoll
    assert db.q("SELECT presence_seen FROM staff WHERE id=?", (sid,))[0]["presence_seen"] == 0     # kein Zeitpunkt in der Datenbank


def test_only_the_person_switches_presence_on(bridge):
    @admin
    async def scenario(b):
        sid = b.db.save_staff({"name": "Petra", "number": "**610", "msn": "", "status": "off"})
        r = await b.http.put(f"/api/staff/{sid}", json={"name": "Petra", "number": "**610", "status": "off", "presence_on": 1, "presence_mac": MAC})
        assert r.status_code == 400 and "selbst" in r.json()["detail"]
        assert b.db.q("SELECT presence_on FROM staff WHERE id=?", (sid,))[0]["presence_on"] == 0
        r = await b.http.put(f"/api/staff/{sid}", json={"name": "Petra", "number": "**610", "status": "off", "presence_on": 0, "presence_mac": MAC})
        assert r.status_code == 200                                            # Geraet vorbelegen geht, einschalten nicht
        as_user(b, sid)
        r = await b.http.put("/api/me/staff", json={"presence_on": 1})
        assert r.status_code == 200, r.text
        mine = (await b.http.get("/api/me/staff")).json()
        assert mine["presence_on"] == 1 and mine["presence_mac"] == MAC
        # ausschalten darf auch der Administrator; eingeschaltet lassen beim Speichern anderer Felder auch
        r = await b.http.put(f"/api/staff/{sid}", json={"name": "Petra", "number": "**610", "status": "off", "presence_on": 1, "presence_mac": MAC})
        assert r.status_code == 200
        r = await b.http.put(f"/api/staff/{sid}", json={"name": "Petra", "number": "**610", "status": "off", "presence_on": 0})
        assert r.status_code == 200 and b.db.q("SELECT presence_on FROM staff WHERE id=?", (sid,))[0]["presence_on"] == 0
    run(bridge, scenario)


def test_status_src_manual_and_until(bridge):
    @admin
    async def scenario(b):
        sid = b.db.save_staff({"name": "Petra", "number": "**610", "msn": "", "status": "office"})
        b.db.x("UPDATE staff SET status='dnd', status_src='presence' WHERE id=?", (sid,))
        assert (await b.http.put(f"/api/staff/{sid}/status", json={"status": "off"})).status_code == 200
        assert st(b.db, sid) == {"status": "off", "status_src": "manual"}
        b.db.x("UPDATE staff SET until='2000-01-01T00:00' WHERE id=?", (sid,))
        b.db.list_staff()
        assert st(b.db, sid) == {"status": "office", "status_src": "until"}
        lst = (await b.http.get("/api/staff")).json()
        assert lst[0]["status_src"] == "until"
    run(bridge, scenario)


def test_ui_shows_origin_and_presence_only_in_own_settings():
    import os
    from tests.conftest import ROOT
    html = open(os.path.join(ROOT, "bridge", "app", "static", "index.html"), encoding="utf-8").read()
    help_js = open(os.path.join(ROOT, "bridge", "app", "static", "help.js"), encoding="utf-8").read()
    assert "Status (manuell)" not in html and "srcTxt(" in html                 # Herkunft statt "manuell"
    assert 'id=my_pres_on' in html and 'presence_on:$("#my_pres_on")' in html   # die Person selbst schaltet ein
    assert '${s.presence_on?"checked":"disabled"}' in html                     # Administrator: nur ausschalten
    assert "presence_seen" not in html
    assert "FRITZ!Box Einstellungen" in help_js and "kein Verlauf" in help_js
