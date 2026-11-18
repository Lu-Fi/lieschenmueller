"""DECT- und IP-Rufnummern ueber die Weboberflaeche der Fritzbox (fritzweb): Fake-Fritzbox mit login_sid.lua, query.lua und data.lua
(Formulare edit_dect_num und edit_ipfon_num nach dem Geruest von FRITZ!OS 8.25), dazu die Zweige von /api/fritz/ring-lines und das
Panel "Asterisk antwortet auf"."""
import hashlib
import json
import re
from urllib.parse import unquote

import pytest

from app import astconf, fritz, fritzweb, main
from tests.conftest import run

PW = "geheim-tr064"
SID = "0123456789abcdef"
NUMBERS = ["5550163", "5550164", "5550165", "99613", "99610", "99611", "99612"]     # eigene Rufnummern der Box (num_1 ...)


class FakeBox:
    """Kleine Zustandsmaschine: Telefone User1.. mit ausgehender Nummer, Auswahl (alle/fest) und angehakten Nummern."""

    def __init__(self):
        self.phones = {
            1: {"name": "Mobil -2-", "intern": "610", "type": "0", "all": False, "out": "5550163", "fonbook": "0", "checked": {"5550163"}},
            2: {"name": "Mobil -1-", "intern": "611", "type": "0", "all": True, "out": "5550164", "fonbook": "1", "checked": set()},
            3: {"name": "Fax", "intern": "1", "type": "1", "all": True, "out": "5550163", "fonbook": "0", "checked": set()},
        }
        self.numbers = list(NUMBERS)
        # IP-Telefone nach TR-064-Client-Index (= ip_idx der Weboberflaeche)
        self.ips = {
            0: {"name": "IP-Telefon Ludwig", "intern": "622", "all": False, "out": "5550163", "checked": {"5550163"}},
            1: {"name": "Asterisk", "intern": "621", "all": True, "out": "5550163", "checked": set()},
            2: {"name": "LieschenMueller", "intern": "620", "all": False, "out": "5550164", "checked": {"5550163", "5550164"}},
        }
        self.ip_ignore_saves = 0        # wie ignore_saves, fuer edit_ipfon_num
        self.ip_unreachable = False     # Weboberflaeche nicht erreichbar (Verbindungsfehler)
        self.logins = self.logouts = 0
        self.saves = []
        self.ignore_saves = 0           # so viele Speichervorgaenge melden "ok", aendern aber nichts (Gegenlesen schlaegt fehl)
        self.allow_out = False          # ausgehende Nummer darf sich beim Speichern aendern (nur dect_set_out/ip_set_out)
        self.form_html = None           # eigenes (kaputtes) HTML fuer das Formular
        self.blocktime = 0
        self.challenge = "2$10$" + "a1" * 16 + "$20$" + "b2" * 16

    # --- Darstellung wie die echte Box ---
    def msn_list(self, idx):
        p = self.phones[idx]
        if p["all"]:
            nums = [p["out"]]
        else:
            nums = [p["out"] if p["out"] in p["checked"] else p["out"] + "#"] + sorted(n for n in p["checked"] if n != p["out"])
        nums += [""] * (10 - len(nums))
        return [{"_node": f"MSN{i}", "Number": n} for i, n in enumerate(nums)]

    def form(self, idx):
        if self.form_html is not None:
            return self.form_html
        p = self.phones[idx]
        allids = [{"id": f"uiNum_{k}", "post_id": f"num_{k}", "checked": n in p["checked"] or p["all"], "num": n}
                  for k, n in enumerate(self.numbers, 1)]
        boxes = "".join(f'<input type="checkbox" id="uiNum_{k}" name="num_{k}" value="{n}"{" checked" if n in p["checked"] else ""}>'
                        for k, n in enumerate(self.numbers, 1))
        opts = "".join(f'<option value="{n}"{" selected" if n == p["out"] else ""}>{n}</option>' for n in self.numbers)
        return f"""<div><form>
<input type="text" value="{p['name']}" id="uiViewName" name="name">
<select name="fonbook"><option value="0"{' selected = "selected"' if p['fonbook'] == '0' else ''}>Telefonbuch</option>
<option value="1"{' selected = "selected"' if p['fonbook'] == '1' else ''}>Firma</option></select>
<select id="uiOutNum" name="out_num">{opts}</select>
<input type="radio" name="num_selection" value="all_nums" id="uiAll"{' checked' if p['all'] else ''}>
<input type="radio" name="num_selection" value="sel_nums" id="uiSel"{'' if p['all'] else ' checked'}>
{boxes}
<input type="hidden" name="idx" value="{idx}">
<input type="hidden" name="dect_type" value="MT-F 7.2">
<input type="checkbox" name="intern_ring" checked>
<input type="checkbox" name="unchecked_box" value="x">
<input type="radio" name="ring_mode" value="a"><input type="radio" name="ring_mode" value="b" checked>
<select name="ringtone"><option value="1">Klassik</option><option value="3" selected>Modern</option></select>
<button type="submit" name="btn_save"></button><input type="submit" name="btn_cancel" value="Abbrechen">
</form><script>var g_OutNum="{p['out']}"; var g_max_selectable_nums = 10;
var data = {json.dumps({"allIds": allids})};</script></div>"""

    def ip_form(self, idx):
        """Wie das echte edit_ipfon_num (FRITZ!OS 8.25): Name mit id=uiName, ohne Telefonbuch, ip_idx, ohne var data/g_OutNum."""
        p = self.ips[idx]
        opts = "".join(f'<option value="{n}"{" selected" if n == p["out"] else ""}>{n}</option>' for n in self.numbers)
        boxes = "\n".join(f'<input type="checkbox" id="uiNum_{k}" name="num_{k}" value="{n}" {"checked" if n in p["checked"] else ""}>'
                          for k, n in enumerate(self.numbers, 1))
        return f"""<div><form id="MainForm" name="main_form" method="POST" action="/fon_devices/edit_ipfon_num.lua">
<input type="text" id="uiName" name="name" data-prop-horizontal="true" data-prop-label-width="14"  size="30" value="{p['name']}"/>
<select data-prop-horizontal="true" size="1" id="uiOutNum" name="out_num">{opts}</select>
<input type="radio" id="uiAllNums" name="num_selection" value="all_nums" {"checked" if p["all"] else ""}>
<input type="radio" id="uiSelNums" name="num_selection" value="sel_nums" {"" if p["all"] else "checked"}>
{boxes}
<input type="hidden" value="{idx}" name="ip_idx">
<input type="hidden" value="/fon_devices/fondevices_list.lua?x=1" name="back_to_page">
<button type="submit" name="btn_save" id="uiSave">Übernehmen</button>
</form></div>"""

    # --- HTTP ---
    def http(self, method, path, params=None, data=None):
        params, data = params or {}, data or {}
        if self.ip_unreachable:
            raise fritzweb.FritzWebError("Weboberfläche der Fritzbox nicht erreichbar (ConnectionError)")
        if path == "/login_sid.lua":
            if data.get("logout"):
                self.logouts += 1
                return 200, "<SessionInfo><SID>0000000000000000</SID></SessionInfo>"
            if method == "GET":
                return 200, f"<SessionInfo><SID>0000000000000000</SID><Challenge>{self.challenge}</Challenge><BlockTime>{self.blocktime}</BlockTime></SessionInfo>"
            want = None                     # nur die PBKDF2-Variante ist hier "richtig"
            if self.challenge.startswith("2$"):
                _, it1, s1, it2, s2 = self.challenge.split("$")
                h1 = hashlib.pbkdf2_hmac("sha256", PW.encode(), bytes.fromhex(s1), int(it1))
                want = s2 + "$" + hashlib.pbkdf2_hmac("sha256", h1, bytes.fromhex(s2), int(it2)).hex()
            ok = data.get("username") == "tr064" and data.get("response") == want
            self.logins += ok
            return 200, f"<SessionInfo><SID>{SID if ok else '0000000000000000'}</SID><Challenge>x</Challenge><BlockTime>0</BlockTime></SessionInfo>"
        sid = params.get("sid") or data.get("sid")
        if sid != SID:
            return 403, "forbidden"
        if path == "/query.lua" and "u" not in params:       # mehrere Ausdruecke in einer Anfrage (_query_many)
            out = {}
            for k, v in params.items():
                if k == "sid":
                    continue
                m = re.fullmatch(r"telcfg:settings/Foncontrol/User(\d+)/MSN/list\(Number\)", unquote(v))
                out[k] = self.msn_list(int(m.group(1))) if m else []
            return 200, json.dumps(out)
        if path == "/query.lua":
            u = unquote(params["u"])
            if u == "telcfg:settings/Foncontrol/User/list(Name,Intern,Type,RingOnAllMSNs)":
                rows = [{"_node": "User0", "Name": "", "Intern": "", "Type": "", "RingOnAllMSNs": ""}]
                rows += [{"_node": f"User{i}", "Name": p["name"], "Intern": p["intern"], "Type": p["type"], "RingOnAllMSNs": "1" if p["all"] else "0"}
                         for i, p in self.phones.items()]
                return 200, json.dumps({"u": rows})
            m = re.fullmatch(r"telcfg:settings/Foncontrol/User(\d+)/MSN/list\(Number\)", u)
            if m:
                return 200, json.dumps({"u": self.msn_list(int(m.group(1)))})
            return 200, json.dumps({"u": []})
        if path == "/data.lua" and data.get("page") == "edit_dect_num":
            idx = int(data["idx"])
            if "btn_save" not in data:
                return 200, self.form(idx)
            self.saves.append(dict(data))
            if self.ignore_saves:
                self.ignore_saves -= 1
                return 200, json.dumps({"data": {"redirect": {}, "btn_save": "ok"}})
            p = self.phones[idx]
            if self.allow_out and data["out_num"] != p["out"]:
                assert data["out_num"] in self.numbers
                p["out"] = data["out_num"]
            for k in ("name", "fonbook", "out_num"):
                assert data[k] == {"name": p["name"], "fonbook": p["fonbook"], "out_num": p["out"]}[k]       # unveraendert uebernommen
            if self.form_html is None:          # weitere Felder des Formulars unveraendert mitgesendet, Knoepfe/leere Checkboxen nicht
                assert (data["dect_type"], data["intern_ring"], data["ring_mode"], data["ringtone"]) == ("MT-F 7.2", "on", "b", "3")
                assert "unchecked_box" not in data and "btn_cancel" not in data
            p["all"] = data["num_selection"] == "all_nums"
            p["checked"] = {v for k, v in data.items() if re.fullmatch(r"num_\d+", k)}
            return 200, json.dumps({"data": {"redirect": {}, "btn_save": "ok"}})
        if path == "/data.lua" and data.get("page") == "edit_ipfon_num":
            assert "idx" not in data and "fonbook" not in data
            idx = int(data["ip_idx"])
            if "btn_save" not in data:
                return 200, self.ip_form(idx)
            self.saves.append(dict(data))
            if self.ip_ignore_saves:
                self.ip_ignore_saves -= 1
                return 200, json.dumps({"data": {"redirect": {}}})      # ohne btn_save: das Gegenlesen entscheidet
            p = self.ips[idx]
            if self.allow_out and data["out_num"] != p["out"]:
                assert data["out_num"] in self.numbers
                p["out"] = data["out_num"]
            assert (data["name"], data["out_num"]) == (p["name"], p["out"])
            assert data["back_to_page"] == "/fon_devices/fondevices_list.lua?x=1" and "btn_cancel" not in data
            assert not any("pass" in k.lower() for k in data)                   # nie ein Kennwort
            p["all"] = data["num_selection"] == "all_nums"
            p["checked"] = {v for k, v in data.items() if re.fullmatch(r"num_\d+", k)}
            return 200, json.dumps({"data": {"btn_save": "ok"}})
        return 404, ""


@pytest.fixture
def box(monkeypatch):
    b = FakeBox()
    monkeypatch.setenv("FRITZ_TR064_USER", "tr064")
    monkeypatch.setenv("FRITZ_TR064_PASSWORD", PW)
    monkeypatch.setattr(fritzweb, "_http", b.http)
    fritzweb.cache_clear()
    yield b
    fritzweb.cache_clear()


def test_dect_phones(box):
    ph = fritzweb.dect_phones()
    assert [p["intern"] for p in ph] == ["**610", "**611"]                # User0 leer, Fax (Type 1) kein DECT
    a, b = ph
    assert a == {"idx": 1, "name": "Mobil -2-", "intern": "**610", "ring_all": False, "out_num": "5550163", "out_only": False,
                 "incoming": ["5550163"]}
    assert b["ring_all"] and b["out_num"] == "5550164"
    assert box.logins == 1 and box.logouts == 1


def test_parse_form_real_skeleton(box):
    f = fritzweb.parse_form(box.form(1), 1)
    assert (f["name"], f["fonbook"], f["out_num"], f["selection"]) == ("Mobil -2-", "0", "5550163", "sel_nums")
    assert [n["num"] for n in f["nums"] if n["checked"]] == ["5550163"] and f["nums"][4] == {"post_id": "num_5", "num": "99610", "checked": False}
    assert f["fields"] == {"name": "Mobil -2-", "fonbook": "0", "out_num": "5550163", "idx": "1", "dect_type": "MT-F 7.2",
                           "intern_ring": "on", "ring_mode": "b", "ringtone": "3"}
    # ohne var data: Rueckfall auf die Checkboxen
    html = re.sub(r"var data = .*?;</script>", "</script>", box.form(1), flags=re.S)
    assert fritzweb.parse_form(html, 1)["nums"] == f["nums"]
    for broken in ("<html>Anmeldung</html>", box.form(1).replace('name="idx" value="1"', 'name="idx" value="2"'),
                   box.form(1).replace('value="sel_nums"', 'value="x"'), box.form(1).replace('name="fonbook"', 'name="fb"'),
                   box.form(1).replace(' selected = "selected"', '')):                 # Telefonbuch ohne Auswahl: nicht raten
        with pytest.raises(fritzweb.FritzWebError, match="anders aus als erwartet"):
            fritzweb.parse_form(broken, 1)


def test_assign_and_remove(box):
    assert fritzweb.dect_assign("**610", "99610", True) == "ok"
    assert box.phones[1]["checked"] == {"5550163", "99610"} and not box.phones[1]["all"]
    s = box.saves[-1]
    assert (s["num_1"], s["num_5"], s["num_selection"], s["back_to_page"]) == ("5550163", "99610", "sel_nums", "/fon_devices/fondevices_list.lua")
    assert "num_2" not in s
    assert fritzweb.dect_assign("**610", "99610", True) == "same" and len(box.saves) == 1
    assert fritzweb.dect_assign("610", "99610", False) == "ok"
    assert box.phones[1]["checked"] == {"5550163"}
    assert box.logins == box.logouts == 3


def test_ring_all_is_not_changed(box):
    assert fritzweb.dect_assign("**611", "99611", True) == "all"
    assert box.saves == [] and box.phones[2]["all"]


def test_select_numbers_from_all(box):
    p = fritzweb.dect_select_numbers("**611", ["5550164", "99611"])
    assert not p["ring_all"] and set(p["incoming"]) == {"5550164", "99611"}
    assert not box.phones[2]["all"] and box.phones[2]["checked"] == {"5550164", "99611"}
    with pytest.raises(fritzweb.FritzWebError, match="kennt die Fritzbox nicht"):
        fritzweb.dect_select_numbers("**611", ["0123"])
    with pytest.raises(fritzweb.FritzWebError, match="Höchstens 10"):
        fritzweb.dect_select_numbers("**611", [str(i) for i in range(11)])


def test_verify_failure_restores(box):
    box.ignore_saves = 1                    # Box meldet "ok", uebernimmt aber nichts
    with pytest.raises(fritzweb.FritzWebError, match="alte Stand wurde zurückgeschrieben"):
        fritzweb.dect_assign("**610", "99610", True)
    assert len(box.saves) == 2 and box.saves[1]["num_selection"] == "sel_nums" and "num_5" not in box.saves[1]
    assert box.phones[1]["checked"] == {"5550163"}
    box.ignore_saves = 1                    # "alle" -> fest, schlaegt fehl: zurueck auf "alle"
    with pytest.raises(fritzweb.FritzWebError):
        fritzweb.dect_select_numbers("**611", ["5550164", "99611"])
    assert box.saves[-1]["num_selection"] == "all_nums" and box.phones[2]["all"]


def test_max_ten(box):
    box.numbers = [str(9000 + i) for i in range(10)] + ["99610"]
    box.phones[1].update(out="9000", checked={str(9000 + i) for i in range(10)})
    with pytest.raises(fritzweb.FritzWebError, match="schon 10"):
        fritzweb.dect_assign("**610", "99610", True)
    assert box.saves == []


def test_unknown_number_and_broken_form(box):
    with pytest.raises(fritzweb.FritzWebError, match="kennt das Formular"):
        fritzweb.dect_assign("**610", "99699", True)
    box.form_html = "<html><body>Sitzung abgelaufen</body></html>"
    with pytest.raises(fritzweb.FritzWebError, match="anders aus als erwartet"):
        fritzweb.dect_assign("**610", "99610", True)
    assert box.saves == [] and box.logins == box.logouts == 2
    with pytest.raises(fritzweb.FritzWebError, match="kein DECT-Telefon"):
        fritzweb.dect_assign("**699", "99699", True)


def test_login_failed_and_blocked(box, monkeypatch):
    monkeypatch.setenv("FRITZ_TR064_PASSWORD", "falsch")
    with pytest.raises(fritzweb.FritzWebError, match="fehlgeschlagen") as e:
        fritzweb.dect_phones()
    assert "falsch" not in str(e.value) and box.logouts == 0
    box.blocktime = 30
    with pytest.raises(fritzweb.FritzWebError, match="30 s"):
        fritzweb.dect_phones()
    monkeypatch.delenv("FRITZ_TR064_PASSWORD")
    with pytest.raises(fritzweb.FritzWebError, match="fehlen"):
        fritzweb.dect_phones()


def test_md5_response():
    exp = "1234567z-" + hashlib.md5("1234567z-äbc".encode("utf-16le")).hexdigest()
    assert fritzweb._response("1234567z", "äbc") == exp


# --- API /api/fritz/ring-lines ---

def _rows():
    return [dict(phone="**610", name="Mobil -2-", kind="DECT", ring="99610", exists=True, ours=True, registrar="1.2.3.4", own_ip="1.2.3.4", assigned=None),
            dict(phone="**611", name="Mobil -1-", kind="DECT", ring="99611", exists=True, ours=True, registrar="1.2.3.4", own_ip="1.2.3.4", assigned=None),
            dict(phone="**622", name="SIP", kind="IP", ring="99622", exists=True, ours=True, registrar="1.2.3.4", own_ip="1.2.3.4", assigned=False)]


@pytest.fixture
def api(bridge, box, monkeypatch):
    monkeypatch.setattr(fritz, "ring_overview", _rows)
    monkeypatch.setattr(fritz, "cached_phones", lambda: [{"number": r["phone"], "name": r["name"], "kind": r["kind"]} for r in _rows()])

    async def none():
        return ""
    monkeypatch.setattr(main, "ring_apply", none)
    return bridge


def test_api_dect_on_off(api, box):
    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            rows = {r["ring"]: r for r in (await b.http.get("/api/fritz/ring-lines")).json()}
            assert rows["99610"]["assigned"] is False and not rows["99610"]["ring_all_phone"] and rows["99610"]["out_num"] == "5550163"
            assert rows["99611"]["assigned"] is True and rows["99611"]["ring_all_phone"]
            assert rows["99622"]["assigned"] is False and not rows["99622"]["ring_all_phone"]          # IP: unveraendert aus TR-064
            d = (await b.http.put("/api/fritz/ring-lines/99610", json={"on": True})).json()
            assert d["assigned"] == "ok" and not d["assign_error"] and not d["ring_all_phone"]
            assert "99610" in box.phones[1]["checked"] and astconf.stored_ring_ok() == ["99610"] and astconf.ring_ok() == ["99613", "99610"]
            rows = {r["ring"]: r for r in (await b.http.get("/api/fritz/ring-lines")).json()}
            assert rows["99610"]["assigned"] is True and rows["99610"]["ok"]
            # "alle": nicht umgestellt und NICHT ueber 99611 waehlen (sonst klingelten alle Telefone mit "alle" mit)
            d = (await b.http.put("/api/fritz/ring-lines/99611", json={"on": True})).json()
            assert d["assigned"] == "all" and d["ring_all_phone"] and box.phones[2]["all"] and "99611" not in astconf.ring_ok()
            # bewusst umstellen
            r = await b.http.put("/api/fritz/ring-lines/99611", json={"select_numbers": ["5550164", "99611"]})
            assert r.status_code == 200 and box.phones[2]["checked"] == {"5550164", "99611"} and not box.phones[2]["all"]
            # ausschalten: Zuweisung entfernen, austragen
            d = (await b.http.put("/api/fritz/ring-lines/99610", json={"on": False})).json()
            assert not d["assign_error"] and box.phones[1]["checked"] == {"5550163"}
            assert "99610" not in astconf.ring_lines() and "99610" not in astconf.ring_ok()
        finally:
            main.app.dependency_overrides.clear()
    run(api, scenario)


def test_api_select_numbers_checks(api, box):
    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            put = lambda body: b.http.put("/api/fritz/ring-lines/99611", json=body)        # noqa: E731
            assert (await put({"select_numbers": ["5550164", "99611"]})).status_code == 400   # noch nicht eingeschaltet
            await put({"on": True})
            assert (await put({"select_numbers": ["5550164"]})).status_code == 400               # ohne die eigene 99NNN
            assert (await put({"select_numbers": ["99611", "99610"]})).status_code == 400        # fremde 99NNN
            assert (await put({"select_numbers": "99611"})).status_code == 400
            assert (await put({"select_numbers": ["99611"] + [str(i) for i in range(10)]})).status_code == 400
            r = await put({"select_numbers": ["99611", "0123"]})
            assert r.status_code == 409 and "kennt die Fritzbox nicht" in r.json()["detail"]
            assert box.phones[2]["all"] and box.saves == []
        finally:
            main.app.dependency_overrides.clear()
    run(api, scenario)


def test_api_select_numbers_admin_only(api, box):
    async def scenario(b):
        r = await b.http.put("/api/fritz/ring-lines/99611", json={"select_numbers": ["99611"]})       # nicht angemeldet
        assert r.status_code in (401, 403) and box.saves == [] and box.logins == 0
    run(api, scenario)


def test_api_fallback_when_write_fails(api, box):
    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            box.ignore_saves = 1
            d = (await b.http.put("/api/fritz/ring-lines/99610", json={"on": True})).json()
            assert d["assigned"] is None and "zurückgeschrieben" in d["assign_error"]
            assert "99610" in astconf.ring_lines() and "99610" not in astconf.ring_ok()      # Rueckfall: Haekchen von Hand
            assert (await b.http.put("/api/fritz/ring-lines/99610", json={"assigned": True})).status_code == 200
            assert "99610" in astconf.ring_ok()
            # Weboberflaeche nicht lesbar: assigned None, Haekchen bleibt bedienbar
            box.form_html = None
            box.challenge = "kaputt"            # MD5-Variante, falsche Antwort -> Anmeldung schlaegt fehl
            rows = {r["ring"]: r for r in (await b.http.get("/api/fritz/ring-lines")).json()}
            assert rows["99610"]["assigned"] is None and rows["99610"]["ok"] and not rows["99610"]["ring_all_phone"]
        finally:
            main.app.dependency_overrides.clear()
    run(api, scenario)


def test_auto_select_keeps_real_numbers_and_ring_all_and_back(bridge, monkeypatch):
    """Umstellen wie "alle", nur ohne die 99NNN anderer Telefone (aber mit der Nummer fuer "alle Telefone klingeln"); zurueck auf "alle"."""
    from app import fritzweb, main, astconf
    calls = []
    monkeypatch.setattr(fritzweb, "box_numbers", lambda intern: ["5550163", "5550164", "99613", "99610", "99612"])
    monkeypatch.setattr(fritzweb, "dect_select_numbers", lambda intern, nums: calls.append(("sel", intern, list(nums))))
    monkeypatch.setattr(fritzweb, "dect_set_all", lambda intern: calls.append(("all", intern)))

    async def none():
        return ""
    monkeypatch.setattr(main, "ring_apply", none)

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            main._ring_set("ring_lines", "99612", True)
            d = (await b.http.put("/api/fritz/ring-lines/99612", json={"select_numbers": "auto", "dry": True})).json()
            assert d["numbers"] == ["5550163", "5550164", "99613", "99612"] and not calls      # ohne 99610, nichts geschrieben
            assert d["owners"] == []
            sid = b.db.save_staff({"name": "Petra", "number": "**612", "msn": "5550164", "status": "office"})
            d = (await b.http.put("/api/fritz/ring-lines/99612", json={"select_numbers": "auto", "dry": True})).json()
            assert d["numbers"] == ["5550164", "99613", "99612"] and d["owners"] == ["Petra"]          # nur die Nummern der Benutzerin
            b.db.x("DELETE FROM staff WHERE id=?", (sid,))
            assert (await b.http.put("/api/fritz/ring-lines/99612", json={"select_numbers": "auto"})).status_code == 200
            assert calls[-1] == ("sel", "**612", ["5550163", "5550164", "99613", "99612"]) and "99612" in astconf.ring_ok()
            assert b.db.get_meta("ring_was_all:99612", "") == "1"
            assert (await b.http.put("/api/fritz/ring-lines/99612", json={"ring_all_phone": True})).status_code == 200
            assert calls[-1] == ("all", "**612") and "99612" not in astconf.ring_ok() and not b.db.get_meta("ring_was_all:99612", "")
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)


def test_get_has_no_side_effect_cleanup_reads_fresh(api, box):
    """GET meldet nur stale; POST .../cleanup schaltet ring_ok erst nach frischem Nachlesen ab (veralteter Cache schaltet nichts ab)."""
    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            main._ring_set("ring_lines", "99611", True)
            main._ring_set("ring_ok", "99611", True)            # 611 steht in der Box auf "alle"
            rows = {r["ring"]: r for r in (await b.http.get("/api/fritz/ring-lines")).json()}
            assert rows["99611"]["stale"] and rows["99611"]["ok"] and not rows["99610"]["stale"]
            assert "99611" in astconf.ring_ok()                 # GET aendert nichts
            d = (await b.http.post("/api/fritz/ring-lines/cleanup")).json()
            assert d["cleaned"] == ["99611"] and "99611" not in astconf.ring_ok()
            # Cache sagt noch "alle", die Box hat inzwischen eine feste Auswahl (z. B. gleichzeitiges Umstellen): nichts abschalten
            main._ring_set("ring_ok", "99611", True)
            assert (await b.http.get("/api/fritz/ring-lines")).json()[1]["stale"]
            box.phones[2].update(all=False, checked={"5550164", "99611"})
            assert (await b.http.get("/api/fritz/ring-lines")).json()[1]["stale"]          # (noch) aus dem Cache
            d = (await b.http.post("/api/fritz/ring-lines/cleanup")).json()
            assert d["cleaned"] == [] and "99611" in astconf.ring_ok()
            assert not (await b.http.get("/api/fritz/ring-lines")).json()[1]["stale"]      # frischer Stand im Cache
        finally:
            main.app.dependency_overrides.clear()
    run(api, scenario)


def test_cache_not_overwritten_by_read_started_before_clear(box):
    """Ein Lesevorgang, der vor cache_clear (Schreiben) begann, speichert seinen womoeglich alten Stand nicht im Cache."""
    real = box.http

    def http(method, path, params=None, data=None):
        if path == "/query.lua" and "u" in (params or {}) and not box.cleared:
            box.cleared = True
            fritzweb.cache_clear()              # waehrend des Lesens wird geschrieben
        return real(method, path, params=params, data=data)
    box.cleared = False
    fritzweb._http = http
    assert fritzweb.dect_phones()[1]["ring_all"]
    assert fritzweb._cache["v"] is None                         # nicht gespeichert
    fritzweb.dect_phones()
    assert fritzweb._cache["v"] is not None and box.logins == 2


def test_off_after_select_restores_all(api, box):
    """Ausschalten nach "Umstellen": zuerst zurueck auf "alle", dann ring_was_all loeschen; scheitert das, Fehler und Meta bleibt
    (die Zeile zeigt dann "auf alle zurueck", auch wenn sie aus ist)."""
    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            put = lambda body: b.http.put("/api/fritz/ring-lines/99611", json=body)        # noqa: E731
            await put({"on": True})
            assert (await put({"select_numbers": ["5550164", "99611"]})).status_code == 200 and not box.phones[2]["all"]
            assert b.db.get_meta("ring_was_all:99611", "") == "1"
            # 1. Zurueckstellen scheitert: Fehler melden, Meta behalten, trotzdem in Asterisk ausgetragen
            box.ignore_saves = 2
            d = (await put({"on": False})).json()
            assert d["assign_error"] and d["was_all"] and not box.phones[2]["all"]
            assert b.db.get_meta("ring_was_all:99611", "") == "1" and "99611" not in astconf.ring_lines()
            rows = {r["ring"]: r for r in (await b.http.get("/api/fritz/ring-lines")).json()}
            assert not rows["99611"]["on"] and rows["99611"]["was_all"] and not rows["99611"]["ring_all_phone"]
            # 2. erneut ein- und ausschalten: jetzt klappt es
            await put({"on": True})
            d = (await put({"on": False})).json()
            assert not d["assign_error"] and d["assigned"] == "all" and not d["was_all"] and box.phones[2]["all"]
            assert not b.db.get_meta("ring_was_all:99611", "")
        finally:
            main.app.dependency_overrides.clear()
    run(api, scenario)


def test_auto_select_owner_numbers_normalized_and_checks_before_dry(bridge, monkeypatch):
    """Umstellen "auto": Benutzer-Nummern in anderer Schreibweise (+49 …) werden auf die Schreibweise der Box abgebildet; passt keine,
    alle echten Nummern plus Hinweis (note). Die Pruefungen (eingerichtet, MAX_NUMS, fremde 99NNN) gelten schon fuer dry."""
    box_nums = ["0305550163", "0305550164", "99613", "99610", "99612"]
    monkeypatch.setattr(fritzweb, "box_numbers", lambda intern: list(box_nums))
    monkeypatch.setattr(fritzweb, "dect_select_numbers", lambda intern, nums: pytest.fail("dry darf nicht schreiben"))

    async def none():
        return ""
    monkeypatch.setattr(main, "ring_apply", none)

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            put = lambda body: b.http.put("/api/fritz/ring-lines/99612", json=dict(body, dry=True))     # noqa: E731
            assert (await put({"select_numbers": "auto"})).status_code == 400            # 99612 noch nicht eingerichtet
            main._ring_set("ring_lines", "99612", True)
            sid = b.db.save_staff({"name": "Petra", "number": "**612", "msn": "+49 30 5550164", "status": "office"})
            d = (await put({"select_numbers": "auto"})).json()
            assert d["numbers"] == ["0305550164", "99613", "99612"] and d["owners"] == ["Petra"] and not d["note"]
            b.db.x("UPDATE staff SET msn='' WHERE id=?", (sid,))
            d = (await put({"select_numbers": "auto"})).json()
            assert d["numbers"] == ["0305550163", "0305550164", "99613", "99612"] and "Petra" in d["note"]
            b.db.x("UPDATE staff SET msn='0401234567' WHERE id=?", (sid,))         # nicht in der Box
            assert (await put({"select_numbers": "auto"})).json()["note"]
            d = (await put({"select_numbers": [" 99612 ", "0305550163 "]})).json()
            assert d["numbers"] == ["99612", "0305550163"]
            assert (await put({"select_numbers": ["99612", "99610"]})).status_code == 400   # fremde 99NNN schon im dry
            box_nums[:] = [f"03055501{i:02d}" for i in range(11)] + ["99612"]
            b.db.x("UPDATE staff SET msn='' WHERE id=?", (sid,))
            r = await put({"select_numbers": "auto"})
            assert r.status_code == 400 and "Höchstens" in r.json()["detail"]
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)



# --- IP-Telefone (edit_ipfon_num) ---

def test_parse_ip_form_real_skeleton(box):
    f = fritzweb.parse_form(box.ip_form(2), 2, "ip")
    assert (f["kind"], f["name"], f["fonbook"], f["out_num"], f["selection"]) == ("ip", "LieschenMueller", None, "5550164", "sel_nums")
    assert [n["num"] for n in f["nums"] if n["checked"]] == ["5550163", "5550164"]
    assert f["fields"] == {"name": "LieschenMueller", "out_num": "5550164", "ip_idx": "2",
                           "back_to_page": "/fon_devices/fondevices_list.lua?x=1"}
    assert fritzweb.ip_form(2) == {"idx": 2, "name": "LieschenMueller", "out_num": "5550164", "ring_all": False,
                                   "incoming": ["5550163", "5550164"], "numbers": NUMBERS}
    html = box.ip_form(2)
    for broken in (html.replace('value="2" name="ip_idx"', 'value="1" name="ip_idx"'),
                   html.replace('name="ip_idx"', 'name="idx"'),
                   html.replace(' selected>', '>'),                                       # ausgehende Nummer ohne Auswahl
                   html.replace("</form>", '<input type="password" name="pw" value="x"></form>')):
        with pytest.raises(fritzweb.FritzWebError, match="anders aus als erwartet"):
            fritzweb.parse_form(broken, 2, "ip")
    with pytest.raises(fritzweb.FritzWebError, match="anders aus als erwartet"):
        fritzweb.parse_form(box.form(1), 1, "ip")                                        # DECT-Formular ist kein IP-Formular


def test_ip_assign_and_remove(box):
    box.numbers = NUMBERS + ["99622"]
    assert fritzweb.ip_assign(0, "99622", True) == "ok"
    assert box.ips[0]["checked"] == {"5550163", "99622"} and not box.ips[0]["all"]
    s = box.saves[-1]
    assert (s["page"], s["ip_idx"], s["num_selection"], s["num_1"]) == ("edit_ipfon_num", "0", "sel_nums", "5550163")
    assert fritzweb.ip_assign(0, "99622", True) == "same" and len(box.saves) == 1
    assert fritzweb.ip_assign(0, "99622", False) == "ok" and box.ips[0]["checked"] == {"5550163"}
    with pytest.raises(fritzweb.FritzWebError, match="keine ankommende"):
        fritzweb.ip_assign(0, "5550163", False)
    with pytest.raises(fritzweb.FritzWebError, match="kennt das Formular"):
        fritzweb.ip_assign(0, "99699", True)
    assert box.logins == box.logouts == 5


def test_ip_all_nums(box):
    box.numbers = NUMBERS + ["99621"]
    assert fritzweb.ip_assign(1, "99621", True) == "all" and box.saves == []           # "alle": nicht umgestellt
    st = fritzweb.ip_set_numbers(1, ["5550163", "99621"])
    assert not st["ring_all"] and set(st["incoming"]) == {"5550163", "99621"} and not box.ips[1]["all"]
    st = fritzweb.ip_set_numbers(1, [], ring_all=True)
    assert st["ring_all"] and box.ips[1]["all"] and box.saves[-1]["num_selection"] == "all_nums"
    n = len(box.saves)
    assert fritzweb.ip_set_numbers(1, [], ring_all=True)["ring_all"] and len(box.saves) == n        # schon "alle": nichts
    with pytest.raises(fritzweb.FritzWebError, match="kennt die Fritzbox nicht"):
        fritzweb.ip_set_numbers(1, ["0123"])
    with pytest.raises(fritzweb.FritzWebError, match="Mindestens"):
        fritzweb.ip_set_numbers(1, [])


def test_ip_verify_failure_restores(box):
    box.ip_ignore_saves = 1                 # Box antwortet ohne btn_save und uebernimmt nichts: Gegenlesen zeigt den alten Stand
    with pytest.raises(fritzweb.FritzWebError, match="alte Stand wurde zurückgeschrieben") as e:
        fritzweb.ip_set_numbers(2, ["5550165"])
    assert e.value.wrote and len(box.saves) == 2 and box.saves[1]["num_selection"] == "sel_nums"
    assert {k for k in box.saves[1] if k.startswith("num_")} == {"num_1", "num_2", "num_selection"}
    assert box.ips[2]["checked"] == {"5550163", "5550164"}
    real = box.http
    saves = []

    def http500(method, path, params=None, data=None):     # Zurueckschreiben scheitert (HTTP 500): ehrlich melden
        if "btn_save" in (data or {}):
            saves.append(1)
            if len(saves) == 2:
                return 500, ""
        return real(method, path, params=params, data=data)
    box.ip_ignore_saves = 1
    fritzweb._http = http500
    with pytest.raises(fritzweb.FritzWebError, match="NICHT zurückgeschrieben"):
        fritzweb.ip_set_numbers(2, ["5550165"])
    box.ip_ignore_saves = 1                 # Antwort ohne btn_save, aber gespeichert: Gegenlesen entscheidet -> ok

    def http(method, path, params=None, data=None):
        st, txt = real(method, path, params=params, data=data)
        if (data or {}).get("page") == "edit_ipfon_num" and "btn_save" in (data or {}) and "btn_save" not in txt:
            p = box.ips[int(data["ip_idx"])]
            p["all"], p["checked"] = data["num_selection"] == "all_nums", {v for k, v in data.items() if re.fullmatch(r"num_\d+", k)}
        return st, txt
    fritzweb._http = http
    assert fritzweb.ip_set_numbers(2, ["5550165"])["incoming"] == ["5550165"]


# --- Panel "Asterisk (LieschenMueller) antwortet auf" ---

class AstFC:
    """TR-064 des Panels: Clients aus der Fake-Box, GetNumbers, GetInfoEx (erlaubte Kennwortzeichen), SetClient3 (protokolliert)."""

    def __init__(self, box):
        self.box, self.set = box, []

    def call_action(self, svc, action, **kw):
        if action == "X_AVM-DE_GetNumberOfClients":
            return {"NewX_AVM-DE_NumberOfClients": len(self.box.ips)}
        if action == "X_AVM-DE_GetClient3":
            p = self.box.ips[kw["NewX_AVM-DE_ClientIndex"]]
            inc = ("<Item><Number></Number><Type>eAllCalls</Type><Index>0</Index></Item>" if p["all"] else
                   "".join(f"<Item><Number>{n}</Number><Type>eVoIP</Type><Index>0</Index></Item>" for n in sorted(p["checked"])))
            return {"NewX_AVM-DE_ClientUsername": p["name"].lower(), "NewX_AVM-DE_PhoneName": p["name"], "NewX_AVM-DE_ClientId": "",
                    "NewX_AVM-DE_InternalNumber": p["intern"], "NewX_AVM-DE_OutGoingNumber": p["out"],
                    "NewX_AVM-DE_InComingNumbers": f"<List>{inc}</List>"}
        if action == "X_AVM-DE_GetNumbers":
            return {"NewNumberList": "<List>" + "".join(f"<Item><Number>{n}</Number><Type>eVoIP</Type><Index>{i}</Index></Item>"
                                                        for i, n in enumerate(self.box.numbers)) + "</List>"}
        if action == "GetInfoEx":
            return {"NewX_AVM-DE_ClientPasswordAllowedChars": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._",
                    "NewX_AVM-DE_ClientPasswordMinChars": "8", "NewX_AVM-DE_ClientPasswordMaxChars": "32"}
        if action == "X_AVM-DE_SetClient3":
            self.set.append(kw)
            return {}
        raise AssertionError(action)


@pytest.fixture
def ast(box, monkeypatch):
    fc = AstFC(box)
    monkeypatch.setattr(fritz, "_conn", lambda: fc)
    monkeypatch.setenv("FRITZ_USER", "LieschenMueller")
    monkeypatch.setenv("FRITZ_PASSWORD", "geheim!Kennwort")         # "!" geht nicht ueber TR-064 (803)
    return fc


def test_asterisk_panel_via_web_despite_write_block(box, ast):
    d = fritz.asterisk_numbers()
    assert d["write_block"] == "" and d["write_via"] == "web"
    assert [n["number"] for n in d["numbers"] if n["on"]] == ["5550163", "5550164"]
    assert "99613" not in [n["number"] for n in d["numbers"]]
    d = fritz.set_asterisk_numbers(["5550165"])
    assert box.ips[2]["checked"] == {"5550165"} and not box.ips[2]["all"] and ast.set == []
    assert [n["number"] for n in d["numbers"] if n["on"]] == ["5550165"]
    with pytest.raises(RuntimeError, match="Ungueltige"):
        fritz.set_asterisk_numbers(["99613"])
    box.ip_ignore_saves = 1                 # gespeichert, aber falscher Stand: Fehler, KEIN Rueckfall auf TR-064
    with pytest.raises(fritzweb.FritzWebError, match="zurückgeschrieben"):
        fritz.set_asterisk_numbers(["5550163"])
    assert ast.set == [] and box.ips[2]["checked"] == {"5550165"}


def test_asterisk_panel_fallback_tr064(box, ast, monkeypatch):
    box.ip_unreachable = True
    d = fritz.asterisk_numbers()            # TR-064 gesperrt (Kennwort) und Weboberflaeche nicht erreichbar: Hinweis mit beidem
    assert "TR-064" in d["write_block"] and "Weboberfläche" in d["write_block"] and d["write_via"] == ""
    with pytest.raises(RuntimeError, match="nicht erreichbar"):
        fritz.set_asterisk_numbers(["5550165"])
    assert ast.set == []
    monkeypatch.setenv("FRITZ_PASSWORD", "geheimKennwort1")         # TR-064 geht: Rueckfall SetClient3
    assert fritz.asterisk_numbers()["write_via"] == "tr064"
    fritz.set_asterisk_numbers(["5550165"])
    assert len(ast.set) == 1 and ast.set[0]["NewX_AVM-DE_ClientIndex"] == 2 and "5550165" in ast.set[0]["NewX_AVM-DE_InComingNumbers"]


def test_api_asterisk_put_via_web(bridge, box, ast, monkeypatch):
    monkeypatch.setattr(main, "phones_busy", lambda: False)

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            r = await b.http.put("/api/fritz/asterisk", json={"numbers": ["5550163", "5550165"], "confirm": True})
            assert r.status_code == 200 and box.ips[2]["checked"] == {"5550163", "5550165"} and ast.set == []
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)


# --- Anruferanzeige bei IP-Telefonen ---

@pytest.fixture
def ipapi(bridge, box, ast, monkeypatch):
    def rows():
        ip = fritz.ip_clients(ast)
        out = []
        for ph, c in ip.items():
            if ph not in ("**622", "**621"):
                continue
            ring = "99" + ph[2:]
            out.append(dict(phone=ph, name=c["name"], kind="IP", ring=ring, exists=True, ours=True, registrar="1.2.3.4", own_ip="1.2.3.4",
                            assigned=ring in c["incoming"], client_idx=c["idx"], ip_all=c["all"], ip_out=c["out"]))
        return out
    monkeypatch.setattr(fritz, "ring_overview", rows)
    monkeypatch.setattr(fritz, "_with_conn", lambda fn: fn(ast))
    monkeypatch.setattr(fritz, "cached_phones", lambda: [{"number": r["phone"], "name": r["name"], "kind": "IP"} for r in rows()])
    box.numbers = NUMBERS + ["99622", "99621"]

    async def none():
        return ""
    monkeypatch.setattr(main, "ring_apply", none)
    return bridge


def test_api_ip_on_off_and_all(ipapi, box):
    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            put = lambda ring, body: b.http.put(f"/api/fritz/ring-lines/{ring}", json=body)       # noqa: E731
            rows = {r["ring"]: r for r in (await b.http.get("/api/fritz/ring-lines")).json()}
            assert rows["99622"]["assigned"] is False and rows["99622"]["out_num"] == "5550163" and not rows["99622"]["ring_all_phone"]
            assert rows["99621"]["ring_all_phone"] and rows["99621"]["assigned"] is True
            d = (await put("99622", {"on": True})).json()
            assert d["assigned"] == "ok" and not d["assign_error"] and box.ips[0]["checked"] == {"5550163", "99622"}
            assert astconf.stored_ring_ok() == ["99622"] and astconf.ring_ok() == ["99613", "99622"]   # 99613: ring_all
            d = (await put("99622", {"on": False})).json()
            assert not d["assign_error"] and box.ips[0]["checked"] == {"5550163"} and "99622" not in astconf.ring_lines()
            # "alle": nicht umstellen, nicht ueber 99621 waehlen; Umstellen und beim Ausschalten zurueck auf "alle"
            d = (await put("99621", {"on": True})).json()
            assert d["assigned"] == "all" and d["ring_all_phone"] and box.ips[1]["all"] and "99621" not in astconf.ring_ok()
            d = (await put("99621", {"select_numbers": "auto", "dry": True})).json()
            assert "99621" in d["numbers"] and "99622" not in d["numbers"]
            assert (await put("99621", {"select_numbers": "auto"})).status_code == 200
            assert not box.ips[1]["all"] and "99621" in box.ips[1]["checked"] and "99621" in astconf.ring_ok()
            assert b.db.get_meta("ring_was_all:99621", "") == "1"
            d = (await put("99621", {"on": False})).json()
            assert d["assigned"] == "all" and not d["was_all"] and box.ips[1]["all"] and not b.db.get_meta("ring_was_all:99621", "")
            # wieder "alle" und noch ueber 99621 gewaehlt (z. B. in der Fritzbox umgestellt): stale, cleanup schaltet ab
            main._ring_set("ring_lines", "99621", True)
            main._ring_set("ring_ok", "99621", True)
            assert {r["ring"]: r for r in (await b.http.get("/api/fritz/ring-lines")).json()}["99621"]["stale"]
            assert (await b.http.post("/api/fritz/ring-lines/cleanup")).json()["cleaned"] == ["99621"]
        finally:
            main.app.dependency_overrides.clear()
    run(ipapi, scenario)


def test_api_ip_web_unreachable_keeps_manual(ipapi, box):
    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            box.ip_unreachable = True
            d = (await b.http.put("/api/fritz/ring-lines/99622", json={"on": True})).json()
            assert d["assigned"] is None and "nicht erreichbar" in d["assign_error"] and "99622" not in astconf.ring_ok()
            box.ips[0]["checked"].add("99622")       # von Hand in der Fritzbox zugewiesen: TR-064 liest es, kein Fehler
            d = (await b.http.put("/api/fritz/ring-lines/99622", json={"on": True})).json()
            assert not d["assign_error"] and "99622" in astconf.ring_ok()
        finally:
            main.app.dependency_overrides.clear()
    run(ipapi, scenario)


# --- ausgehende Rufnummer (dect_set_out / ip_set_out) ---

def test_dect_set_out_and_back(box):
    box.allow_out = True
    st = fritzweb.dect_set_out("**610", "99610")
    assert st["out_num"] == "99610" and st["out_was"] == "5550163" and box.phones[1]["out"] == "99610"
    assert box.phones[1]["checked"] == {"5550163"} and not box.phones[1]["all"]          # ankommend unveraendert
    s = box.saves[-1]
    assert s["out_num"] == "99610" and s["num_selection"] == "sel_nums" and s["num_1"] == "5550163" and s["ringtone"] == "3"
    n = len(box.saves)
    assert fritzweb.dect_set_out("**610", "99610")["out_was"] == "99610" and len(box.saves) == n      # schon so: nichts
    st = fritzweb.dect_set_out("**611", "99611")                                         # "alle" bleibt "alle"
    assert box.phones[2]["out"] == "99611" and box.phones[2]["all"] and box.saves[-1]["num_selection"] == "all_nums"
    assert fritzweb.dect_set_out("**610", "5550163")["out_num"] == "5550163" and box.phones[1]["out"] == "5550163"


def test_dect_set_out_errors(box):
    box.allow_out = True
    with pytest.raises(fritzweb.FritzWebError, match="nicht als ausgehende"):
        fritzweb.dect_set_out("**610", "99699")
    assert box.saves == []
    box.ignore_saves = 1                    # Box meldet "ok", uebernimmt nichts: zurueckgeschrieben, Fehler
    with pytest.raises(fritzweb.FritzWebError, match="bisherige wurde zurückgeschrieben") as e:
        fritzweb.dect_set_out("**610", "99610")
    assert e.value.wrote and len(box.saves) == 2 and box.saves[1]["out_num"] == "5550163" and box.phones[1]["out"] == "5550163"
    box.ignore_saves = 2                    # auch das Zurueckschreiben bleibt wirkungslos: ehrlich melden
    box.phones[1]["out"] = "5550163"
    real = box.http

    def http(method, path, params=None, data=None):
        st, txt = real(method, path, params=params, data=data)
        if (data or {}).get("page") == "edit_dect_num" and "btn_save" in (data or {}) and data["out_num"] == "99610":
            box.phones[1]["out"] = "5550165"        # jemand anderes stellt gleichzeitig um
        return st, txt
    fritzweb._http = http
    with pytest.raises(fritzweb.FritzWebError, match="NICHT zurückgeschrieben"):
        fritzweb.dect_set_out("**610", "99610")
    fritzweb._http = real
    with pytest.raises(fritzweb.FritzWebError, match="kein DECT-Telefon"):
        fritzweb.dect_set_out("**699", "99610")
    box.ip_unreachable = True
    with pytest.raises(fritzweb.FritzWebError, match="nicht erreichbar") as e:
        fritzweb.dect_set_out("**610", "99610")
    assert not e.value.wrote


def test_ip_set_out(box):
    box.allow_out = True
    box.numbers = NUMBERS + ["99622"]
    st = fritzweb.ip_set_out(0, "99622")
    assert st["out_num"] == "99622" and st["out_was"] == "5550163" and box.ips[0]["out"] == "99622"
    assert box.ips[0]["checked"] == {"5550163"} and box.saves[-1]["back_to_page"] == "/fon_devices/fondevices_list.lua?x=1"
    n = len(box.saves)
    assert fritzweb.ip_set_out(0, "99622")["out_was"] == "99622" and len(box.saves) == n
    with pytest.raises(fritzweb.FritzWebError, match="nicht als ausgehende"):
        fritzweb.ip_set_out(0, "0123")
    box.ip_ignore_saves = 1                 # Antwort ohne btn_save, nichts uebernommen: Gegenlesen -> zurueck, Fehler
    with pytest.raises(fritzweb.FritzWebError, match="bisherige wurde zurückgeschrieben") as e:
        fritzweb.ip_set_out(0, "5550163")
    assert e.value.wrote and box.ips[0]["out"] == "99622" and box.saves[-1]["out_num"] == "99622"
    with pytest.raises(fritzweb.FritzWebError, match="Client-Index"):
        fritzweb.ip_set_out("x", "5550163")
