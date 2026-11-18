"""Klingel-Rufnummern je Telefon (99NNN): Pruefung, fritz.conf mit aors und Endpoints, Einstellung ueber die API."""
import pytest

from app import astconf, fritzconf, main
from tests.conftest import run


def test_clean_ring_lines():
    assert fritzconf.clean_ring_lines("99610, 99622;99613 99610") == ["99610", "99622", "99613"]     # 99613 wie jede andere
    with pytest.raises(ValueError):
        fritzconf.clean_ring_lines("99610, 0171")


def test_render_has_ring_endpoints():
    t = fritzconf.render("LieschenMueller", "geheim123", "192.168.178.1", acl=False, ring=["99610", "99622"])
    assert "aors=fritz,99610,99622\n" in t
    assert t.count("[99610]") == 2 and t.count("[99622]") == 2 and "context=from-fritzring" in t
    plain = fritzconf.render("LieschenMueller", "geheim123", "192.168.178.1", acl=False)
    assert "aors=fritz\n" in plain and "[99613]" not in plain and "[99610]" not in plain


def test_settings_ring_lines(bridge, tmp_path, monkeypatch):
    path = tmp_path / "fritz.conf"
    monkeypatch.setattr(astconf, "SIP_FILE", str(path))
    monkeypatch.setattr(astconf, "db_creds", lambda: ("LieschenMueller", "geheim123", "192.168.178.1"))
    sent = []

    async def cmd(c, *a, **k):
        sent.append(c)
        return ""
    monkeypatch.setattr(main.ami, "command", cmd)

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        r = await b.http.put("/api/settings", json={"ring_lines": "99610 99622"})
        assert r.status_code == 200 and r.json()["ring_lines"] == "99610,99622"
        assert "aors=fritz,99613,99610,99622" in path.read_text()      # 99613: Voreinstellung ring_all and sent == ["module reload res_pjsip.so"]
        assert (await b.http.put("/api/settings", json={"ring_lines": "0171"})).status_code == 400
        main.app.dependency_overrides.pop(main.auth, None)
    from tests.conftest import run
    run(bridge, scenario)


def test_endpoint_only_when_assigned():
    t = fritzconf.render("LieschenMueller", "geheim123", "192.168.178.1", acl=False, ring=["99610", "99622"], ring_ok=["99622"])
    assert "aors=fritz,99610,99622\n" in t                           # Anmeldung fuer beide moeglich
    assert t.count("[99610]") == 1 and t.count("[99622]") == 2       # gewaehlt wird nur ueber die zugewiesene


def test_assigned_flag_api(bridge, monkeypatch):
    from app import fritz
    monkeypatch.setattr(fritz, "ring_overview", lambda: [dict(phone="**622", name="SIP", kind="IP", ring="99622", exists=True, ours=True,
                                                              registrar="1.2.3.4", own_ip="1.2.3.4", assigned=True),
                                                         dict(phone="**610", name="DECT", kind="DECT", ring="99610", exists=True, ours=True,
                                                              registrar="1.2.3.4", own_ip="1.2.3.4", assigned=None)])

    async def none():
        return ""
    monkeypatch.setattr(main, "ring_apply", none)

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        for n in ("99622", "99610"):
            assert (await b.http.put(f"/api/fritz/ring-lines/{n}", json={"on": True})).status_code == 200
        assert astconf.stored_ring_lines() == ["99622", "99610"] and astconf.stored_ring_ok() == ["99622"]       # IP-Telefon: gelesen; DECT: offen
        assert (await b.http.put("/api/fritz/ring-lines/99610", json={"assigned": True})).status_code == 200
        assert astconf.stored_ring_ok() == ["99622", "99610"] and astconf.ring_ok() == ["99613", "99622", "99610"]
        assert (await b.http.put("/api/fritz/ring-lines/99610", json={"on": False})).status_code == 200
        assert astconf.stored_ring_lines() == ["99622"] and astconf.stored_ring_ok() == ["99622"]
        main.app.dependency_overrides.pop(main.auth, None)
    from tests.conftest import run
    run(bridge, scenario)


def test_99613_ring_all_flag_follows_setting(bridge, monkeypatch):
    """Die Zeile heisst nur "klingelt alle Telefone", wenn die Einstellung ring_all wirklich darauf steht (leer = ausgeschaltet)."""
    from app import fritz
    monkeypatch.setattr(fritz, "ring_overview", lambda: [dict(phone="**613", name="Moritz", kind="DECT", ring="99613", exists=True,
                                                              ours=True, registrar="1.2.3.4", own_ip="1.2.3.4", assigned=None)])

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            r = (await b.http.get("/api/fritz/ring-lines")).json()[0]
            assert "fixed" not in r and r["ring_all"] and r["on"]   # Standard: 99613 laesst alle Telefone klingeln
            b.db.set_meta("ring_all", "")
            r = (await b.http.get("/api/fritz/ring-lines")).json()[0]
            assert not r["ring_all"] and not r["on"]                # nicht eingetragen: nicht mehr fest
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)


def test_ring_all_row_shows_which_dect_phones_ring(bridge, monkeypatch):
    from app import fritz, fritzweb
    monkeypatch.setattr(fritz, "ring_overview", lambda: [dict(phone="**613", name="Moritz", kind="DECT", ring="99613", exists=True,
                                                              ours=True, registrar="1.2.3.4", own_ip="1.2.3.4", assigned=None)])
    monkeypatch.setattr(fritzweb, "dect_phones", lambda fresh=False: [
        dict(idx=1, name="Clemens", intern="**610", ring_all=False, out_num="1", out_only=False, incoming=["1", "99610"]),
        dict(idx=2, name="Moritz", intern="**613", ring_all=True, out_num="1", out_only=False, incoming=["1"]),
        dict(idx=3, name="Empfang", intern="**611", ring_all=False, out_num="1", out_only=False, incoming=["1", "99613"])])

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            r = (await b.http.get("/api/fritz/ring-lines")).json()[0]
            assert r["rings_on"] == ["Moritz", "Empfang"] and r["rings_not"] == ["Clemens"]
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)


def test_ui_ring_and_number_buttons_use_data_attributes():
    """Telefonnamen/Rufnummern aus der Fritzbox nie als JS-String in onclick/onchange: esc macht ' zu &#39;, das der HTML-Parser
    vor dem Ausfuehren zurueckwandelt (DECT-Name "O'Brien" = kaputter Knopf, "x');alert(1);('" = Skript)."""
    import os
    import re
    from tests.conftest import ROOT
    html = open(os.path.join(ROOT, "bridge", "app", "static", "index.html"), encoding="utf-8").read()
    handlers = re.findall(r'\bon(?:click|change|input)="([^"]*)"', html)
    bad = [h for h in handlers if re.search(r"'\$\{esc\((?:r\.(?:name|ring|out_num)|n\.number)", h)]
    assert not bad
    assert "ringArg" not in html
    assert 'data-ring="${esc(r.ring)}" data-name="${esc(r.name)}"' in html and 'data-num="${esc(n.number)}"' in html
    for f in ("ringLineSet", "ringAssigned", "ringRetry", "ringSelect", "ringAllBack"):
        assert re.search(r"async function " + f + r"\(el[,)][^\n]*\n const \{ring,name\}=ringD\(el\);", html), f
