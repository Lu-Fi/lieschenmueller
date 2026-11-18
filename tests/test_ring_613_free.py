"""99613 ist keine feste Klingel-Rufnummer mehr: Migration bestehender Installationen, 99613 normal ein/aus, die Nummer fuer
"alle Telefone klingeln" (meta ring_all) immer mit AOR+Endpoint und nicht abschaltbar."""
from app import astconf, fritz, fritzconf, main
from tests.conftest import run

CREDS = ("LieschenMueller", "geheim123", "192.168.178.1")


def _rows():
    return [dict(phone=f"**{n}", name=nm, kind="DECT", ring=f"99{n}", exists=True, ours=True, registrar="1.2.3.4", own_ip="1.2.3.4",
                 assigned=None) for n, nm in (("611", "Empfang"), ("613", "Moritz"))]


def _setup(monkeypatch, tmp_path):
    path = tmp_path / "fritz.conf"
    monkeypatch.setattr(astconf, "SIP_FILE", str(path))
    monkeypatch.setattr(astconf, "db_creds", lambda: CREDS)
    monkeypatch.setattr(fritz, "ring_overview", _rows)
    monkeypatch.setattr(fritz, "cached_phones", lambda: [{"number": r["phone"], "name": r["name"], "kind": "analog"} for r in _rows()])

    async def cmd(c, *a, **k):
        return ""
    monkeypatch.setattr(main.ami, "command", cmd)
    return path


def test_template_has_no_fixed_99613():
    from tests.conftest import ROOT
    import os
    tpl = open(os.path.join(ROOT, "asterisk", "templates", "pjsip.conf.tpl"), encoding="utf-8").read()
    assert "[99613]" not in tpl
    plain = fritzconf.render(*CREDS, acl=False)
    assert "aors=fritz\n" in plain and "[99613]" not in plain
    assert fritzconf.clean_ring_lines("99613 99610") == ["99613", "99610"]


def test_migration_once(bridge):
    b = bridge
    b.db.set_meta("ring_lines", "99610")
    b.db.set_meta("ring_ok", "")
    assert astconf.migrate_613() is True
    assert astconf.stored_ring_lines() == ["99610", "99613"] and astconf.stored_ring_ok() == ["99613"]   # Standard: ring_all = 99613
    assert b.db.get_meta("ring_613_free") == "1"
    # "alle Telefone klingeln" aus (wie im Testaufbau): 99613 nur eingetragen, nicht als zugewiesen markiert
    b.db.set_meta("ring_613_free", "")
    b.db.set_meta("ring_all", "")
    b.db.set_meta("ring_lines", "99610")
    b.db.set_meta("ring_ok", "")
    assert astconf.migrate_613() is True
    assert astconf.stored_ring_lines() == ["99610", "99613"] and astconf.stored_ring_ok() == []
    b.db.set_meta("ring_all", "99613")
    # danach normal abschaltbar: die Migration traegt sie nicht wieder ein
    b.db.set_meta("ring_lines", "99610")
    b.db.set_meta("ring_ok", "")
    assert astconf.migrate_613() is False and astconf.stored_ring_lines() == ["99610"]


def test_migration_runs_at_start_and_keeps_99613(bridge, tmp_path, monkeypatch):
    path = _setup(monkeypatch, tmp_path)
    bridge.db.set_meta("ring_all", "99611")
    assert astconf.sync_fritz(str(path)) == "written"
    t = path.read_text()
    assert "aors=fritz,99611,99613\n" in t and t.count("[99613]") == 2 and t.count("[99611]") == 2


def test_ring_all_number_always_has_endpoint(bridge):
    b = bridge
    assert astconf.ring_all_lines() == ["99613"]                     # Voreinstellung neuer Installationen
    b.db.set_meta("ring_all", "99611+**612+99614")
    b.db.set_meta("ring_lines", "99610")
    b.db.set_meta("ring_ok", "")
    assert astconf.ring_lines() == ["99611", "99614", "99610"] and astconf.ring_ok() == ["99611", "99614"]
    assert b.db.get_meta("ring_lines") == "99610"                   # nicht gespeichert, nur wirksam
    t = fritzconf.render(*CREDS, acl=False, ring=astconf.ring_lines(), ring_ok=astconf.ring_ok())
    assert t.count("[99611]") == 2 and t.count("[99614]") == 2 and t.count("[99610]") == 1 and "[99613]" not in t
    b.db.set_meta("ring_all", "")
    assert astconf.ring_lines() == ["99610"] and astconf.ring_ok() == []


def test_99613_normal_on_off_and_ring_all_locked(bridge, tmp_path, monkeypatch):
    path = _setup(monkeypatch, tmp_path)
    applied = []
    orig = main.ring_apply

    async def apply():
        applied.append(1)
        return await orig()
    monkeypatch.setattr(main, "ring_apply", apply)
    monkeypatch.setattr(fritz, "ring_numbers", lambda fresh=False: [{"number": "99611"}, {"number": "99613"}])

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            astconf.migrate_613()
            rows = {r["ring"]: r for r in (await b.http.get("/api/fritz/ring-lines")).json()}
            assert "fixed" not in rows["99613"] and rows["99613"]["ring_all"] and rows["99613"]["on"] and not rows["99611"]["ring_all"]
            # 99613 steht in ring_all: nicht abschaltbar
            r = await b.http.put("/api/fritz/ring-lines/99613", json={"on": False})
            assert r.status_code == 409 and "alle Telefone klingeln" in r.json()["detail"]
            assert (await b.http.put("/api/fritz/ring-lines/99613", json={"assigned": False})).status_code == 409
            # Empfang (99611) einschalten und als Grundleitung setzen
            assert (await b.http.put("/api/fritz/ring-lines/99611", json={"on": True})).status_code == 200
            n = len(applied)
            r = await b.http.put("/api/settings", json={"ring_all": "99611"})
            assert r.status_code == 200 and r.json()["ring_all"] == "99611" and "warn" not in r.json() and len(applied) == n + 1
            rows = {r["ring"]: r for r in (await b.http.get("/api/fritz/ring-lines")).json()}
            assert rows["99611"]["ring_all"] and not rows["99613"]["ring_all"]
            # 99613 jetzt normal ausschaltbar; 99611 gesperrt
            assert (await b.http.put("/api/fritz/ring-lines/99613", json={"on": False})).status_code == 200
            assert "99613" not in astconf.ring_lines() and "99613" not in astconf.ring_ok()
            t = path.read_text()
            assert "aors=fritz,99611\n" in t and "[99613]" not in t and t.count("[99611]") == 2
            assert (await b.http.put("/api/fritz/ring-lines/99611", json={"on": False})).status_code == 409
            # wieder einschalten wie jede andere Nummer
            assert (await b.http.put("/api/fritz/ring-lines/99613", json={"on": True})).status_code == 200
            assert "99613" in astconf.ring_lines()
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)


def test_ring_all_unknown_number_warns_and_applies(bridge, tmp_path, monkeypatch):
    path = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(fritz, "ring_numbers", lambda fresh=False: [{"number": "99613"}])

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            r = await b.http.put("/api/settings", json={"ring_all": "99611+99613"})
            assert r.status_code == 200 and "99611" in r.json()["warn"] and "nicht angelegt" in r.json()["warn"]
            t = path.read_text()
            assert t.count("[99611]") == 2 and t.count("[99613]") == 2           # trotzdem eingerichtet
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)


def test_cleanup_keeps_ring_all_number(bridge, monkeypatch):
    from app import fritzweb
    bridge.db.set_meta("ring_all", "99611")
    bridge.db.set_meta("ring_lines", "99611,99610")
    bridge.db.set_meta("ring_ok", "99611,99610")
    monkeypatch.setattr(fritz, "cached_phones", lambda: [{"number": "**611", "name": "E", "kind": "DECT"},
                                                         {"number": "**610", "name": "M", "kind": "DECT"}])
    monkeypatch.setattr(fritzweb, "dect_phones", lambda fresh=False: [
        dict(idx=1, name="E", intern="**611", ring_all=True, out_num="1", out_only=False, incoming=["1"]),
        dict(idx=2, name="M", intern="**610", ring_all=True, out_num="1", out_only=False, incoming=["1"])])

    async def none():
        return ""
    monkeypatch.setattr(main, "ring_apply", none)

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            d = (await b.http.post("/api/fritz/ring-lines/cleanup")).json()
            assert d["cleaned"] == ["99610"] and astconf.stored_ring_ok() == ["99611"]
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)


def test_ui_and_help_no_fixed_99613():
    import os
    from tests.conftest import ROOT
    html = open(os.path.join(ROOT, "bridge", "app", "static", "index.html"), encoding="utf-8").read()
    help_js = open(os.path.join(ROOT, "bridge", "app", "static", "help.js"), encoding="utf-8").read()
    assert "r.fixed" not in html and "fest – klingelt alle Telefone" not in html and "über die Rufnummer <b>99613</b>" not in html
    assert 'id: "klingelnummern"' in help_js and "Klingel-Rufnummer 99613</b>" not in help_js
