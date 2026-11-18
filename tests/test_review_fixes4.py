"""Review Paket 4: Live-Stand fuer neu zugewiesene Rufnummern, "nicht stoeren" ohne ring_all, Vorschau ohne fremde Nummern,
Klingeln ohne doppelte Ziele, Rueckfall nach "nimmt nicht ab", Rueckfrage-Antwort mit Leerzeichen, mehrere Nachrichten an
denselben Empfaenger, Nummernvergleich mit Vorwahl, laengste MSN, Waehlregel bis 23:59."""
import json
import time
import uuid
from types import SimpleNamespace

from app import main
from tests.conftest import run
from tests.test_routing import call, ring, staff


def _user(sid):
    return {"id": 77, "username": "petra", "role": "user", "staff_id": sid, "disabled": 0}


def test_live_sees_calls_on_newly_assigned_number(bridge, monkeypatch):
    async def scenario(b):
        sid = b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": "0301111", "status": "office"})
        b.db.set_meta(b.db.msn_since_key(sid, "0301111"), time.time() - 60)          # Nummer vor einer Minute zugewiesen
        u = _user(sid)
        cid = b.db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction) VALUES(?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), time.time(), "030123456", "0301111", "Assistent", "aktiv", "in"))
        c = SimpleNamespace(alive=True, meta={"call_id": cid, "from": "030123456", "direction": "in"}, agent={"name": "Assistent"},
                            after=None, notes={}, messages_left=lambda: 0)
        monkeypatch.setattr(main, "active_calls", lambda: [c])
        ru = str(uuid.uuid4())
        main.CALLS.put(ru, "ringing", {"id": cid, "caller": "030123456", "called": "0301111", "t": time.time(), "info": "x"})
        main.CALLS.put(ru, "human", {"id": cid, "caller": "030123456", "called": "0301111", "direction": "in", "since": time.time(),
                                     "rec": False, "user_id": None, "chan": ""})
        try:
            snap = main.live_snapshot(u)
            assert [x["id"] for x in snap["calls"]] == [cid]
            assert [x["id"] for x in snap["ringing"]] == [cid]
            assert [x["id"] for x in snap["humans"]] == [cid]
            assert main.active_call(cid, u) is c                                          # Aktionen (Durchstellen, Auflegen) ohne 404
            other = b.db.save_staff({"name": "Andere", "number": "**613", "msn": "", "status": "office"})
            assert main.live_snapshot(_user(other))["calls"] == []                        # fremde Nummer weiter unsichtbar
        finally:
            main.CALLS.take(ru, "ringing")
            main.CALLS.take(ru, "human")
    run(bridge, scenario)


def test_dnd_and_schedule_do_not_ring_all(bridge):
    async def scenario(b):
        ring(b.db)
        sid = staff(b.db, "Petra Muster", "**610", status="dnd")
        u, r = call(b)
        assert await r == "ok"                                                  # nicht ueber 99613 (alle Telefone) klingeln
        assert main.CALLS.field(u, "pending")["for_staff"][0]["status"] == "dnd"
        sch = json.dumps([{"days": [0, 1, 2, 3, 4, 5, 6], "from": "00:00", "to": "23:59", "status": "off"}])
        b.db.x("UPDATE staff SET status='office', use_hours=1, schedule=? WHERE id=?", (sch, sid))
        assert await call(b)[1] == "ok"                                         # Zeitplan "nicht erreichbar": ebenso
        assert await call(b, hdr="<sip:0309999@fritz.box>")[1] == "fdial:20:99613"    # Nummer ohne Mitarbeiter: wie bisher alle Telefone
        aid = b.db.save_agent({"name": "Durchsteller", "mode": "transfer", "transfer_to": "**699"})
        b.db.x("UPDATE call_rules SET agent_id=?", (aid,))
        assert await call(b)[1] == "dial:**699"                                 # Durchstell-Agent per Anrufregel wirkt weiter
    run(bridge, scenario)


def test_my_preview_hides_numbers_of_others(bridge):
    async def scenario(b):
        ring(b.db)
        dep = staff(b.db, "Peter Vertreter", "**630", msn="", status="away", away_number="01709876543")
        sid = staff(b.db, "Petra Muster", "**610", status="off", deputy_id=dep, away_number="01701112223")
        main.app.dependency_overrides[main.user_auth] = lambda: _user(sid)
        try:
            d = (await b.http.post("/api/me/preview", json={"caller": "030555444"})).json()
        finally:
            main.app.dependency_overrides.pop(main.user_auth, None)
        assert d["via_deputy"] and d["target"] == "Peter Vertreter" and d["number"] == ""
        assert "Vertretung Peter Vertreter" in d["text"] and "01709876543" not in d["text"] + d["reason"] + "".join(d["steps"])
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            d = (await b.http.post(f"/api/staff/{sid}/preview", json={"caller": "030555444"})).json()
        finally:
            main.app.dependency_overrides.pop(main.auth, None)
        assert d["number"] == "01709876543" and "(01709876543)" in d["text"]           # Admin-Vorschau unveraendert
        b.db.x("UPDATE staff SET status='away' WHERE id=?", (sid,))
        main.app.dependency_overrides[main.user_auth] = lambda: _user(sid)
        try:
            d = (await b.http.post("/api/me/preview", json={"caller": "030555444"})).json()
        finally:
            main.app.dependency_overrides.pop(main.user_auth, None)
        assert d["number"] == "01701112223" and "01701112223" in d["text"]              # eigene Nummer bleibt sichtbar
    run(bridge, scenario)


def test_shared_number_rings_each_phone_once(bridge):
    async def scenario(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610")
        b.db.save_staff({"kind": "group", "name": "Zentrale", "number": "**610, **622", "msn": "0301111"})
        u, r = call(b)
        assert await r == "fdial:20:**610+**622"                                # nicht **610+**610+**622
        assert b.call_row(u)["agent_name"] == "Durchstellen: Petra Muster, Zentrale"
    run(bridge, scenario)


def test_fallback_marks_only_rung_staff_and_allows_retry(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Guten Tag."), ("hear", "Bitte nochmal Petra."), ("tool", "connect_employee", {"name": "Petra"}),
                         ("say", "Moment.")])

    async def scenario(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610")
        staff(b.db, "Peter Pause", "**620", status="dnd", note="im Termin")
        assert await call(b, u=u)[1] == "fdial:20:**610"
        assert await b.internal("fallback", uuid=u) == "ok"                      # Petra nahm nicht ab
        fs = {x["name"]: x for x in main.CALLS.field(u, "pending")["for_staff"]}
        assert (fs["Petra Muster"]["status"], fs["Petra Muster"]["note"]) == ("off", "hat den Anruf nicht angenommen")
        assert fs["Peter Pause"]["status"] == "dnd" and fs["Peter Pause"]["note"] != "hat den Anruf nicht angenommen"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        s = b.genai.sessions[0]
        assert "- Petra Muster: erreichbar – hat gerade nicht abgenommen" in s.prompt    # kein Widerspruch zu "nicht erreichbar"
        assert s.tool_results[0][1]["ok"] is True                                 # auf Wunsch nochmal verbinden (kein screen_tried)
        assert await b.internal("after", uuid=u) == "dial:**610:default"
    run(bridge, scenario)


def test_screen_answer_parts_joined_with_space():
    from app.call import screen_consent_parts
    assert screen_consent_parts(["Ja, aber jetzt", "nicht"]) == (False, "Verneinung gehört")      # vorher "jetztnicht" = Ja
    assert screen_consent_parts(["Ja, nic", "ht verbinden"])[0] is False         # Wort im Stueck getrennt: Verneinung zaehlt
    assert screen_consent_parts(["Ja,", "verbinden Sie"]) == (True, "")
    assert screen_consent_parts([" Ja", " gerne."]) == (True, "")


def test_second_message_for_same_recipient_is_appended(bridge, monkeypatch):
    from app import plugins
    u = str(uuid.uuid4())
    seen = []
    orig_emit = plugins.emit

    async def emit(event, *args):
        if event == "call_end":
            seen.append(args[0])
        return await orig_emit(event, *args)
    monkeypatch.setattr(plugins, "emit", emit)
    bridge.genai.script([
        ("say", "Guten Tag."),
        ("tool", "note_message", {"empfaenger": "Frau Meier", "nachricht": "Bitte zurueckrufen"}),
        ("tool", "note_message", {"empfaenger": "Frau Meier", "nachricht": "Unterlagen sind unterwegs"}),
        ("tool", "end_call"),
    ])

    async def scenario(b):
        assert await b.internal("call", uuid=u, frm="030123456", to="s", hdr="<sip:0309999@fritz.box>", chan="PJSIP/fritz-1") == "ok"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        notes = json.loads(b.call_row(u)["notes"])
        assert notes["Nachricht für Frau Meier"] == "Bitte zurueckrufen\n\nUnterlagen sind unterwegs"     # nicht ueberschrieben
        assert seen and seen[0].messages_left() == 2                            # Banner zaehlt beide (Animation auch bei der zweiten)
        assert main.call_mod.Call.messages_left(SimpleNamespace(notes=notes)) == 1     # ohne Zaehler: Schluessel wie bisher
    run(bridge, scenario)


def test_same_number_compares_area_codes():
    from app import db, dialrules, remote
    for same in (db.same_number, remote._same, dialrules._same):
        assert not same("030 12345678", "040 12345678")                         # vorher gleich (letzte 9 Ziffern)
        assert same("030 12345678", "+49 30 12345678") and same("0049 30 12345678", "03012345678")
        assert same("12345678", "030 12345678")                                 # eine Seite ohne Vorwahl: Rueckfall auf das Ende
        assert same("**610", "610") and not same("**610", "**611")              # interne Nummern
        assert not same("", "030 12345678")
        assert same("+43 1 2345678", "0043 1 2345678")
    assert dialrules._same("0172555123", "0172555*") and not dialrules._same("0173555123", "0172555*")


def test_dialrule_list_with_area_code(bridge):
    async def scenario(b):
        ring(b.db)
        rules = [{"name": "Hamburg", "caller_mode": "list", "callers": "040 12345678", "action": "forward", "target": "0171999"}]
        staff(b.db, "Petra Muster", "**610", dialrules=rules)
        assert await call(b, frm="03012345678")[1] == "fdial:20:**610"         # Berlin, gleiche Endziffern: Regel greift nicht
        assert await call(b, frm="+494012345678")[1] == "fdial:20:0171999"
    run(bridge, scenario)


def test_longest_msn_wins(bridge):
    async def scenario(b):
        b.db.save_staff({"name": "Anna Kurz", "number": "**611", "msn": "12345", "status": "office"})
        b.db.save_staff({"name": "Berta Lang", "number": "**612", "msn": "912345", "status": "office"})
        assert b.db.canonical_msn("030912345") == "912345"                       # vorher je nach Reihenfolge 12345
        assert [s["name"] for s in b.db.staffs_for_number(["030912345"])] == ["Berta Lang"]
        assert [s["name"] for s in b.db.staffs_for_number(["030812345"])] == ["Anna Kurz"]
        assert b.db.canonical_msn("12345") == "12345"
        b.db.save_staff({"kind": "group", "name": "Zentrale", "number": "**610", "msn": "912345"})
        assert [s["name"] for s in b.db.staffs_for_number(["030912345"])] == ["Berta Lang", "Zentrale"]   # geteilte Nummer: alle
    run(bridge, scenario)


def test_presence_keeps_vacation_with_until(bridge):
    import datetime
    from app import presence
    db = bridge.db
    presence._seen.clear()
    mac = "AA:BB:CC:DD:EE:09"
    later = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    sid = db.save_staff({"name": "Petra", "number": "**610", "msn": "", "status": "off", "until": later, "note": "Urlaub",
                         "presence_on": 1, "presence_mac": mac}, presence_self=True)
    st = lambda: db.q("SELECT status FROM staff WHERE id=?", (sid,))[0]["status"]
    assert presence.tick(lambda m: m == mac, 1_000_000.0) == [] and st() == "off"     # Urlaub bleibt "nicht erreichbar"
    db.x("UPDATE staff SET until='' WHERE id=?", (sid,))
    assert presence.tick(lambda m: m == mac, 1_000_100.0) == [("Petra", "off", "dnd")]   # ohne "bis": wie bisher nicht stoeren


def test_connect_contact_uses_staff_mobile_only_when_reachable(bridge):
    steps = [("say", "Mit wem?"), ("tool", "connect_contact", {"name": "Petra Handy"}), ("say", "Moment."), ("tool", "end_call")]
    for _ in range(3):
        bridge.genai.script(steps)

    async def scenario(b):
        sid = b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": "", "status": "off", "note": "Urlaub"})
        b.db.save_staff({"name": "Moritz Intern", "number": "**611", "msn": "", "status": "office"})
        b.db.save_contact({"name": "Petra Muster", "number": "01701234567"})

        async def one(i):
            u = str(uuid.uuid4())
            assert await b.internal("call", uuid=u, frm="**611", to="s", hdr="<sip:0309999@fritz.box>") == "ok"   # darf waehlen
            sock = await b.audiosocket(u)
            await b.wait_ended(sock)
            return u, b.genai.sessions[i].tool_results[0][1]
        u, res = await one(0)
        assert "fehler" in res and "note_message" in res["fehler"]                # nicht erreichbar: kein Handy aus dem Telefonbuch
        assert "01701234567" not in str(res)
        dep = b.db.save_staff({"name": "Peter Vertreter", "number": "**630", "msn": "", "status": "office"})
        b.db.x("UPDATE staff SET deputy_id=? WHERE id=?", (dep, sid))
        u, res = await one(1)
        assert "Vertretung: Peter Vertreter" in res["fehler"] and "connect_employee" in res["fehler"]
        b.db.x("UPDATE staff SET status='away', deputy_id=NULL WHERE id=?", (sid,))
        u, res = await one(2)
        assert res.get("ok") is True                                             # ausser Haus erreichbar: das Handy wie bisher
        assert (await b.internal("after", uuid=u)).startswith("dial:01701234567")
    run(bridge, scenario)


def test_dialrule_until_2359_covers_last_minute():
    import datetime
    from app import dialrules
    r = dialrules.normalize([{"name": "Abend", "caller_mode": "any", "from": "18:00", "to": "23:59", "action": "ai"}], lambda n: n)[0]
    at = lambda h, m: datetime.datetime(2026, 11, 17, h, m)
    assert dialrules.matches(r, "030123", "", at(23, 59), lambda n: False)       # vorher: 23:59 nicht mehr in der Regel
    assert dialrules.matches(r, "030123", "", at(18, 0), lambda n: False)
    assert not dialrules.matches(r, "030123", "", at(17, 59), lambda n: False)
    r2 = dict(r, to="22:00")
    assert not dialrules.matches(r2, "030123", "", at(22, 0), lambda n: False)   # sonst bleibt "bis" ausschliesslich


def test_warm_agent_skips_empty_texts(bridge, monkeypatch):
    """Ansagen vorab: kein leerer Text an die Sprachausgabe (sonst TimeoutError bei jedem Start)."""
    from app import voices
    said = []

    async def fake(voice, text, *a, **k):
        said.append(text)
        return b""
    monkeypatch.setattr(voices, "sample", fake)

    async def scenario(b):
        b.db.x("UPDATE rec_rules SET enabled=0")
        a = dict(b.db.list_agents()[0], notice_on=0)
        await main.warm_agent(a)
        assert said and "" not in said
    run(bridge, scenario)
