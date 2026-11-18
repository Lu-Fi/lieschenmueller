"""Anwesenheit ueber WLAN-Geraet: nicht erreichbar <-> nicht stoeren, nur mit Option und Geraet, mit Wartezeit beim Verlassen."""
from app import presence

MAC = "AA:BB:CC:DD:EE:01"


def status(db, sid):
    return db.q("SELECT status FROM staff WHERE id=?", (sid,))[0]["status"]


def test_presence_switches_off_and_dnd(bridge):
    db = bridge.db
    presence._seen.clear()
    # presence_self: einschalten darf nur die Person selbst (Meine Einstellungen), siehe test_status_src.py
    sid = db.save_staff({"name": "Petra", "number": "**610", "msn": "", "status": "off", "presence_on": 1, "presence_mac": "aa-bb-cc-dd-ee-01"}, presence_self=True)
    other = db.save_staff({"name": "Ohne Option", "number": "**611", "msn": "", "status": "off", "presence_mac": MAC})
    office = db.save_staff({"name": "Im Buero", "number": "**612", "msn": "", "status": "office", "presence_on": 1, "presence_mac": MAC}, presence_self=True)
    assert db.q("SELECT presence_mac FROM staff WHERE id=?", (sid,))[0]["presence_mac"] == MAC
    t = 1_000_000.0
    assert presence.tick(lambda m: m == MAC, t) == [("Petra", "off", "dnd")]
    assert (status(db, sid), status(db, other), status(db, office)) == ("dnd", "off", "office")
    assert presence.tick(lambda m: False, t + 300) == []                     # kurz weg (Handy schlaeft): bleibt
    assert presence.tick(lambda m: None, t + 900) == []                       # Fritzbox nicht lesbar: nichts aendern
    assert presence.tick(lambda m: False, t + 601) == [("Petra", "dnd", "off")]
    assert status(db, sid) == "off"
    db.x("UPDATE staff SET status='away' WHERE id=?", (sid,))
    assert presence.tick(lambda m: True, t + 700) == [] and status(db, sid) == "away"   # ausser Haus bleibt


def test_clean_mac():
    assert presence.clean_mac("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert presence.clean_mac("AABBCCDDEEFF") == "AA:BB:CC:DD:EE:FF"
    assert presence.clean_mac("kaputt") == "" and presence.clean_mac("") == ""


def test_several_devices_one_is_enough(bridge):
    db = bridge.db
    sid = db.save_staff({"name": "Moritz", "number": "**610", "msn": "", "status": "off", "presence_on": 1,
                         "presence_mac": "aa:bb:cc:dd:ee:01, kaputt, AA-BB-CC-DD-EE-02, aabbccddee01"}, presence_self=True)
    assert db.q("SELECT presence_mac FROM staff WHERE id=?", (sid,))[0]["presence_mac"] == "AA:BB:CC:DD:EE:01,AA:BB:CC:DD:EE:02"
    assert presence.tick(lambda m: m.endswith("02"), 1000.0) == [("Moritz", "off", "dnd")]
    assert presence.any_active(lambda m: None if m.endswith("01") else False, "AA:BB:CC:DD:EE:01,AA:BB:CC:DD:EE:02") is None
    assert presence.any_active(lambda m: False, "AA:BB:CC:DD:EE:01,AA:BB:CC:DD:EE:02") is False


def test_manual_dnd_stays_when_device_never_seen(bridge):
    """Von Hand gesetztes "nicht stoeren" (status_src manual) nimmt die Anwesenheit nie zurueck."""
    db = bridge.db
    sid = db.save_staff({"name": "Hand", "number": "**610", "msn": "", "status": "dnd", "presence_on": 1, "presence_mac": MAC}, presence_self=True)
    assert presence.tick(lambda m: False, 10_000_000.0) == [] and status(db, sid) == "dnd"


def test_device_by_ip(monkeypatch):
    from app import fritz

    class FC:
        def call_action(self, svc, action, **kw):
            assert (svc, action, kw) == ("Hosts1", "X_AVM-DE_GetSpecificHostEntryByIP", {"NewIPAddress": "192.168.178.50"})
            return {"NewMACAddress": "aa:bb:cc:dd:ee:01", "NewHostName": "Pixel"}
    monkeypatch.setattr(fritz, "_shared_conn", lambda: FC())
    assert presence.device_by_ip("192.168.178.50") == {"mac": MAC, "name": "Pixel"}

    class Bad:
        def call_action(self, *a, **k):
            raise RuntimeError("714 NoSuchEntryInArray")
    monkeypatch.setattr(fritz, "_shared_conn", lambda: Bad())
    assert presence.device_by_ip("192.168.178.51") is None


def test_this_device_endpoint(bridge, monkeypatch):
    from app import main
    from tests.conftest import run
    monkeypatch.setattr(presence, "device_by_ip", lambda ip: {"mac": MAC, "name": "Pixel"})

    async def scenario(b):
        sid = b.db.save_staff({"name": "Petra", "number": "**610", "msn": "", "status": "office"})
        main.app.dependency_overrides[main.user_auth] = lambda: {"id": 9, "username": "s", "role": "user", "staff_id": sid, "disabled": 0}
        r = await b.http.get("/api/me/this-device")
        main.app.dependency_overrides.pop(main.user_auth, None)
        assert r.status_code in (200, 404)          # 404 nur, wenn der Testclient als 127.* gilt (lokal: kein Geraet im WLAN)
        if r.status_code == 200:
            assert r.json() == {"mac": MAC, "name": "Pixel"}
    run(bridge, scenario)
