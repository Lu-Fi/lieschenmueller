"""Ausgehende Gespraeche je Benutzer mitschreiben (outrec.py, staff.rec_out): Telefone in der Fritzbox auf ausgehend 99NNN stellen
und zurueck (Fake-Fritzbox aus test_fritzweb), outcall-Entscheidungen, Waechter (Notbetrieb), Sichtbarkeit, /api/me/staff,
Panel "Asterisk antwortet auf" (ausgehende Nummer)."""
import uuid

import pytest

from app import ami, astconf, fritz, fritzweb, main, outrec, users
from tests.conftest import run
from tests.test_fritzweb import NUMBERS, PW, AstFC, FakeBox


@pytest.fixture
def box(monkeypatch):
    b = FakeBox()
    b.allow_out = True
    b.numbers = NUMBERS + ["99622"]
    monkeypatch.setenv("FRITZ_TR064_USER", "tr064")
    monkeypatch.setenv("FRITZ_TR064_PASSWORD", PW)
    monkeypatch.setattr(fritzweb, "_http", b.http)
    fritzweb.cache_clear()
    yield b
    fritzweb.cache_clear()
    outrec._missing.clear()


@pytest.fixture
def env(bridge, box, monkeypatch):
    """Telefone **610/**611 (DECT) und **622 (IP), Klingel-Rufnummern eingerichtet und in der Fritzbox angelegt; angemeldet sind
    die Nummern in env.regs (pjsip show contacts)."""
    fc = AstFC(box)
    monkeypatch.setattr(fritz, "_with_conn", lambda fn: fn(fc))
    monkeypatch.setattr(fritz, "cached_phones", lambda: [{"number": "**610", "name": "Mobil -2-", "kind": "DECT"},
                                                         {"number": "**611", "name": "Mobil -1-", "kind": "DECT"},
                                                         {"number": "**622", "name": "IP-Telefon Ludwig", "kind": "IP"},
                                                         {"number": "**1", "name": "FON1", "kind": "analog"}])
    monkeypatch.setattr(fritz, "ring_numbers", lambda fresh=False: [{"number": n} for n in ("99613", "99610", "99611", "99622")])
    bridge.db.set_meta("ring_lines", "99610,99611,99622")
    bridge.db.set_meta("ring_ok", "99610,99611,99622")
    bridge.regs = {"99610", "99611", "99622"}
    bridge.ami_down = False

    async def command(cmd, timeout_s=15):
        if bridge.ami_down:
            raise ConnectionError("AMI")
        if cmd == "pjsip show contacts":
            return "".join(f"  Contact:  {n}/sip:{n}@192.168.178.1:5060  abc Avail 1.0\n" for n in sorted(bridge.regs))
        return ""
    monkeypatch.setattr(ami, "command", command)
    bridge.box = box
    return bridge


def _staff(b, name="Petra", number="**610", **kw):
    sid = b.db.save_staff(dict({"name": name, "number": number, "msn": "5550163", "status": "office"}, **kw))
    uid = users.create(name.lower(), "Passwort-1234", "user", sid, check=False)
    return sid, b.db.q("SELECT * FROM users WHERE id=?", (uid,))[0]


def _as(u):
    main.app.dependency_overrides[main.user_auth] = lambda: dict(u)


def _admin():
    main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN


def test_switch_on_and_off_via_me_staff(env):
    async def scenario(b):
        sid, u = _staff(b, number="**610, **611, **1, 0301234")
        b.regs.discard("99611")                         # 611 nicht angemeldet: wird uebersprungen (mit Grund)
        _as(u)
        try:
            r = await b.http.put("/api/me/staff", json={"rec_out": "record"})
            assert r.status_code == 200, r.text
            d = r.json()["out_rec"]
            assert d["switched"] == ["**610"] and not d["warnings"]
            why = {s["phone"]: s["why"] for s in d["skipped"]}
            assert "nicht bei Asterisk angemeldet" in why["**611"] and set(why) == {"**611"}       # **1 (analog, ohne 99NNN): kein Telefon dafuer
            assert b.box.phones[1]["out"] == "99610" and b.box.phones[1]["checked"] == {"5550163"}
            assert outrec.orig_get("**610") == {"orig": "5550163", "sid": sid, "kind": "DECT"}
            st = (await b.http.get("/api/me/out-rec")).json()
            assert [(p["phone"], p["state"]) for p in st["phones"]] == [("**610", "active"), ("**611", "off")]
            assert b.db.q("SELECT rec_out FROM staff WHERE id=?", (sid,))[0]["rec_out"] == "record"
            # andere Felder speichern (Status): nichts an der Fritzbox
            n = len(b.box.saves)
            assert (await b.http.put("/api/me/staff", json={"status": "away"})).status_code == 200 and len(b.box.saves) == n
            # aus: zurueck auf die gemerkte Nummer, Meta weg
            d = (await b.http.put("/api/me/staff", json={"rec_out": "off"})).json()["out_rec"]
            assert d["restored"] == ["**610"] and b.box.phones[1]["out"] == "5550163" and not outrec.orig_get("**610")
        finally:
            main.app.dependency_overrides.clear()
    run(env, scenario)


def test_switch_error_rolls_back_and_saves_nothing(env):
    async def scenario(b):
        sid, u = _staff(b, number="**610, **622")
        real = b.box.http

        def http(method, path, params=None, data=None):     # IP-Telefon: Weboberflaeche verweigert das Speichern
            if (data or {}).get("page") == "edit_ipfon_num" and "btn_save" in (data or {}):
                return 500, ""
            return real(method, path, params=params, data=data)
        fritzweb._http = http
        _as(u)
        try:
            r = await b.http.put("/api/me/staff", json={"rec_out": "transcript"})
            assert r.status_code == 409
            msg = r.json()["detail"]
            assert "**622" in msg and "HTTP 500" in msg and "zurückgestellt" in msg and "nicht gespeichert" in msg
            assert b.box.phones[1]["out"] == "5550163" and b.box.ips[0]["out"] == "5550163"     # **610 wieder zurueck
            assert not outrec.switched() and b.db.q("SELECT rec_out FROM staff WHERE id=?", (sid,))[0]["rec_out"] == "off"
            # Telefon steht schon von Hand auf 99NNN: Fehler, nichts gemerkt
            fritzweb._http = real
            b.box.phones[1]["out"] = "99610"
            r = await b.http.put("/api/me/staff", json={"rec_out": "record", "number": "**610"})
            assert r.status_code == 409 and "schon ausgehend auf 99610" in r.json()["detail"] and not outrec.switched()
            b.box.phones[1]["out"] = "5550163"
            # "nur an bestimmte Nummern" ohne Nummern: 400
            assert (await b.http.put("/api/me/staff", json={"rec_out": "numbers", "rec_out_numbers": ""})).status_code == 400
            assert (await b.http.put("/api/me/staff", json={"rec_out": "alles"})).status_code == 400
        finally:
            main.app.dependency_overrides.clear()
    run(env, scenario)


def test_ip_phone_and_admin_editor_and_phone_removed(env):
    async def scenario(b):
        sid, _ = _staff(b, number="**622")
        _admin()
        try:
            st = next(s for s in b.db.list_staff() if s["id"] == sid)
            r = await b.http.put(f"/api/staff/{sid}", json=dict(st, rec_out="numbers", rec_out_numbers="0221555*, 0301234567", rec_out_notice=0))
            assert r.status_code == 200, r.text
            assert r.json()["out_rec"]["switched"] == ["**622"] and b.box.ips[0]["out"] == "99622"
            row = b.db.q("SELECT * FROM staff WHERE id=?", (sid,))[0]
            assert (row["rec_out"], row["rec_out_numbers"], row["rec_out_notice"]) == ("numbers", "0221555*,0301234567", 0)
            assert (await b.http.get(f"/api/staff/{sid}/out-rec")).json()["phones"][0]["state"] == "active"
            # Telefon aus "Klingelt im Buero an" genommen: zurueckgestellt
            st = next(s for s in b.db.list_staff() if s["id"] == sid)
            r = await b.http.put(f"/api/staff/{sid}", json=dict(st, number="**699"))
            assert r.json()["out_rec"]["restored"] == ["**622"] and b.box.ips[0]["out"] == "5550163" and not outrec.switched()
            # Benutzer loeschen stellt zurueck
            st = next(s for s in b.db.list_staff() if s["id"] == sid)
            await b.http.put(f"/api/staff/{sid}", json=dict(st, number="**622"))
            assert b.box.ips[0]["out"] == "99622"
            assert (await b.http.delete(f"/api/staff/{sid}")).status_code == 200
            assert b.box.ips[0]["out"] == "5550163" and not outrec.switched()
        finally:
            main.app.dependency_overrides.clear()
    run(env, scenario)


def test_shared_phone_second_user_skipped_and_ring_line_off_restores(env, monkeypatch):
    async def none():
        return ""
    monkeypatch.setattr(main, "ring_apply", none)

    async def scenario(b):
        a, ua = _staff(b, "Anna", number="**610")
        s2, ub = _staff(b, "Berta", number="**610")
        _as(ua)
        assert (await b.http.put("/api/me/staff", json={"rec_out": "record"})).json()["out_rec"]["switched"] == ["**610"]
        _as(ub)
        d = (await b.http.put("/api/me/staff", json={"rec_out": "record"})).json()["out_rec"]
        assert d["switched"] == [] and "Anna" in d["skipped"][0]["why"]
        main.app.dependency_overrides.clear()
        _admin()
        try:
            d = (await b.http.put("/api/fritz/ring-lines/99610", json={"on": False})).json()
            assert not d["assign_error"], d
            assert b.box.phones[1]["out"] == "5550163" and not outrec.switched() and "99610" not in astconf.ring_lines()
        finally:
            main.app.dependency_overrides.clear()
    run(env, scenario)


# --- outcall ---

def _switch(b, sid, phone="**610", orig="5550163", kind="DECT"):
    outrec._orig_set(phone, orig, sid, kind)


def test_outcall_decisions(env):
    async def scenario(b):
        async def go(to, frm="99610"):
            u = str(uuid.uuid4())
            return await b.internal("outcall", uuid=u, to=to, frm=frm, chan="PJSIP/fritz-1"), b.call_row(u)
        b.db.set_meta("rec_prefix", "000")
        sid, u = _staff(b, rec_out="record")
        # Rufnummer ohne Umstellung (kein out_orig) = Rueckruf aus der Anrufliste: nie mitschreiben
        assert await go("0301234567") == ("dial:0301234567", None)
        _switch(b, sid)
        res, row = await go("0301234567")
        assert res == "recx:o:0301234567" and row["rec_keep"] == 1 and row["rec_transcribe"] == 1
        assert row["caller"] == "Petra (**610, ausgehend)" and row["user_id"] == u["id"] and row["owner_staff_id"] == sid
        assert users.level_for(dict(u), row) == "full"                                  # der Benutzer sieht seine Mitschrift
        where, args = users.sql_scope(dict(u))
        assert row["id"] in [r["id"] for r in b.db.q(f"SELECT id FROM calls WHERE {where}", args)]
        assert users.level_for({"id": 999, "role": "user", "staff_id": None}, row) is None
        # Notruf nie
        assert (await go("112"))[0] == "dial:112" and (await go("000110"))[0] == "dial:110"
        # nur Transkript, ohne Hinweis
        b.db.x("UPDATE staff SET rec_out='transcript', rec_out_notice=0 WHERE id=?", (sid,))
        res, row = await go("0301234567")
        assert res == "recx:oq:0301234567" and row["rec_keep"] == 0 and row["rec_transcribe"] == 1
        # nur an bestimmte Nummern (Anfang* wie bei "nicht stoeren")
        b.db.x("UPDATE staff SET rec_out='numbers', rec_out_numbers='0221555*,+49 30 1234567', rec_out_notice=1 WHERE id=?", (sid,))
        assert (await go("0301234567"))[0] == "recx:o:0301234567"
        assert (await go("022155599"))[0] == "recx:o:022155599"
        assert await go("0309999") == ("dial:0309999", None)
        # mit Vorwahl: Ausgangsregeln wie bisher (Benutzer sichtbar)
        res, row = await go("0000309999")
        assert res.startswith("recx:o") and row["user_id"] == u["id"]
        b.db.x("UPDATE rec_rules SET enabled=0 WHERE direction='out'")
        assert await go("0000309999") == ("dial:0309999", None)
        # Einstellung aus, Notbetrieb oder anderes Telefon: wie bisher (ohne Vorwahl nicht)
        b.db.x("UPDATE staff SET rec_out='record' WHERE id=?", (sid,))
        b.db.set_meta("out_fallback:**610", "1")
        assert await go("0301234567") == ("dial:0301234567", None)
        b.db.x("DELETE FROM meta WHERE key='out_fallback:**610'")
        assert await go("0301234567", frm="99611") == ("dial:0301234567", None)
        b.db.x("UPDATE staff SET rec_out='off' WHERE id=?", (sid,))
        assert await go("0301234567") == ("dial:0301234567", None)
        # aelterer Dialplan ohne from: Endpoint-Name aus dem Kanal
        b.db.x("UPDATE staff SET rec_out='record' WHERE id=?", (sid,))
        u2 = str(uuid.uuid4())
        assert await b.internal("outcall", uuid=u2, to="0301234567", chan="PJSIP/99610-00000001") == "recx:o:0301234567"
    run(env, scenario)


# --- Waechter ---

def test_guard_fallback_and_back(env):
    async def scenario(b):
        sid, u = _staff(b)
        _as(u)
        await b.http.put("/api/me/staff", json={"rec_out": "record"})
        main.app.dependency_overrides.clear()
        b.db.x("INSERT INTO mail_recipients(email,label) VALUES('chef@example.com','Chef')")
        for k, v in (("smtp_host", "smtp.example.com"), ("smtp_user", "u"), ("smtp_password", "p"), ("smtp_from", "lm@example.com")):
            b.db.set_meta(k, v)
        t0 = 1000.0
        assert await outrec.tick(t0) == [] and b.box.phones[1]["out"] == "99610"
        b.regs.discard("99610")                                     # 99610 weg
        assert await outrec.tick(t0 + 30) == []
        assert await outrec.tick(t0 + 100) == [] and b.box.phones[1]["out"] == "99610"        # erst 70 s
        assert await outrec.tick(t0 + 125) == [("fallback", "**610")]
        assert b.box.phones[1]["out"] == "5550163" and outrec.fallback_since("**610") == t0 + 125
        assert len(b.mails) == 1 and "Notbetrieb" in b.mails[0]["subject"] and b.mails[0]["to"] == ["chef@example.com"]
        assert outrec.overview()["99610"]["state"] == "fallback"
        assert await outrec.tick(t0 + 160) == [] and len(b.mails) == 1                         # bleibt, keine zweite Mail
        assert outrec.active_owner("99610") is None
        b.regs.add("99610")                                         # wieder da
        assert await outrec.tick(t0 + 190) == [("back", "**610")]
        assert b.box.phones[1]["out"] == "99610" and not outrec.fallback_since("**610") and len(b.mails) == 2
        assert outrec.active_owner("99610")["id"] == sid
        # Asterisk ganz weg: ebenfalls Notbetrieb nach 90 s
        b.ami_down = True
        assert await outrec.tick(t0 + 200) == []
        assert await outrec.tick(t0 + 300) == [("fallback", "**610")] and "nicht erreichbar" in b.mails[-1]["text"]
        # Zurueckstellen scheitert (Fritzbox weg): kein Notbetrieb gesetzt, naechster Durchlauf versucht es erneut
        b.ami_down = False
        b.box.ip_unreachable = True
        assert await outrec.tick(t0 + 330) == [] and outrec.fallback_since("**610")
        b.box.ip_unreachable = False
        assert await outrec.tick(t0 + 360) == [("back", "**610")]
        # Einstellung im Hintergrund aus (z. B. direkt in der Datenbank): Waechter stellt zurueck
        b.db.x("UPDATE staff SET rec_out='off' WHERE id=?", (sid,))
        assert await outrec.tick(t0 + 400) == [("restored", "**610")] and b.box.phones[1]["out"] == "5550163"
        assert not outrec.switched()
    run(env, scenario)


def test_startup_sync(env):
    async def scenario(b):
        sid, _ = _staff(b, rec_out="record")
        # Meta sagt "umgestellt", die Box steht (nach Neustart/Handarbeit) auf einer anderen echten Nummer: diese wird die bisherige
        _switch(b, sid, orig="5550163")
        b.box.phones[1]["out"] = "5550165"
        await outrec.startup_sync()
        assert b.box.phones[1]["out"] == "99610" and outrec.orig_get("**610")["orig"] == "5550165"
        # Notbetrieb gemerkt, Box steht aber auf 99610: zurueck auf die bisherige
        b.db.set_meta("out_fallback:**610", "5")
        await outrec.startup_sync()
        assert b.box.phones[1]["out"] == "5550165"
        # Einstellung aus: zurueckgestellt und Metas geloescht
        b.db.x("UPDATE staff SET rec_out='off' WHERE id=?", (sid,))
        await outrec.startup_sync()
        assert not outrec.switched() and not outrec.fallback_since("**610")
    run(env, scenario)


# --- Anruferanzeige: Spalte "ausgehend" ---

def test_ring_lines_show_out_rec(env, monkeypatch):
    def rows():
        return [dict(phone="**610", name="Mobil -2-", kind="DECT", ring="99610", exists=True, ours=True, registrar="1", own_ip="1", assigned=None)]
    monkeypatch.setattr(fritz, "ring_overview", rows)

    async def scenario(b):
        sid, _ = _staff(b, rec_out="record")
        _switch(b, sid)
        _admin()
        try:
            r = (await b.http.get("/api/fritz/ring-lines")).json()[0]
            assert r["out_rec"] == {"state": "active", "owner": "Petra", "orig": "5550163"}
            b.db.set_meta("out_fallback:**610", "1")
            assert (await b.http.get("/api/fritz/ring-lines")).json()[0]["out_rec"]["state"] == "fallback"
        finally:
            main.app.dependency_overrides.clear()
    run(env, scenario)


# --- Panel "Asterisk antwortet auf": ausgehende Nummer ---

def test_asterisk_out_number(bridge, box, monkeypatch):
    fc = AstFC(box)
    monkeypatch.setattr(fritz, "_conn", lambda: fc)
    monkeypatch.setenv("FRITZ_USER", "LieschenMueller")

    async def scenario(b):
        _admin()
        try:
            put = lambda body: b.http.put("/api/fritz/asterisk/out", json=body)      # noqa: E731
            assert (await put({"number": "5550163"})).status_code == 400                # ohne Bestaetigung
            r = await put({"number": "5550163", "confirm": True})
            assert r.status_code == 200 and box.ips[2]["out"] == "5550163" and r.json()["outgoing"] == "5550163"
            assert box.ips[2]["checked"] == {"5550163", "5550164"} and fc.set == []            # ankommend unveraendert, kein TR-064
            assert (await put({"number": "99613", "confirm": True})).status_code == 400        # keine Klingel-Rufnummer
            assert (await put({"number": "0123", "confirm": True})).status_code == 400
            box.ip_ignore_saves = 1
            r = await put({"number": "5550165", "confirm": True})
            assert r.status_code == 409 and "zurückgeschrieben" in r.json()["detail"] and box.ips[2]["out"] == "5550163"
        finally:
            main.app.dependency_overrides.clear()
    run(bridge, scenario)
