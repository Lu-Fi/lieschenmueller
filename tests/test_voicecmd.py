"""Sprachbefehle von aussen (Home Assistant): Schluessel, Geraet/Ziel pruefen, "verbinden" ohne KI, "assistent" mit KI."""
import uuid as uuidm

from app import main, voicecmd
from tests.conftest import run


def setup(db, on=True):
    voicecmd._last.clear()
    voicecmd.STATES.clear()
    key = voicecmd.new_key()
    voicecmd.save({"on": on})
    db.save_staff({"name": "Lars Tester", "number": "**611", "msn": "", "status": "office"})
    db.save_contact({"name": "Mama", "number": "0241123456", "note": ""})
    return key


def H(key):
    return {"authorization": f"Bearer {key}", "content-type": "application/json"}


def test_voice_connect_rings_device_then_target(bridge):
    async def scenario(b):
        key = setup(b.db)
        r = await b.http.post("/api/voice/connect", json={"device": "**610", "target": "Mama"}, headers={"authorization": "Bearer falsch"})
        assert r.status_code == 401
        r = await b.http.post("/api/voice/connect", json={"device": "**610", "target": "Mama"}, headers=H(key))
        assert r.status_code == 200, r.text
        assert r.json()["target"] == "Mama"
        await b.settle()
        orig = [c for c in b.ami.calls if c[0] == "originate"]
        assert orig[-1][1] == "**610" and orig[-1][3] == "vc-connect" and orig[-1][4] == {"TARGET": "0241123456"}
        u = orig[-1][2]
        assert b.call_row(u)["status"] == "waehlt"
        assert await b.internal("vc-event", uuid=u, ev="up") == "ok"
        assert b.call_row(u)["status"] == "aktiv"
        assert await b.internal("vc-event", uuid=u, ev="end", st="ANSWER") == "ok"
        assert b.call_row(u)["status"] == "durchgestellt"
        # gleich nochmal dasselbe Geraet: gebremst
        r = await b.http.post("/api/voice/connect", json={"device": "**610", "target": "Mama"}, headers=H(key))
        assert r.status_code == 429
        # X-Api-Key geht auch; Benutzer als Ziel (seine Nummern), Benutzer als Geraet (sein Telefon)
        r = await b.http.post("/api/voice/connect", json={"device": "Lars", "target": "**612"}, headers={"x-api-key": key})
        assert r.status_code == 200 and r.json()["device"] == "**611"
    run(bridge, scenario)


def test_voice_connect_checks(bridge):
    async def scenario(b):
        key = setup(b.db)
        b.db.save_contact({"name": "Mama", "number": "0171555666", "note": "Handy"})
        post = lambda d, k=key: b.http.post("/api/voice/connect", json=d, headers=H(k))
        assert (await post({"device": "**610", "target": "Mama"})).status_code == 409           # zwei Nummern: nachfragen
        assert (await post({"device": "**610", "target": "Niemand Bekanntes"})).status_code == 404
        assert (await post({"device": "**610", "target": "09001234567"})).status_code == 400    # DIAL_BLOCK
        assert (await post({"device": "0241999", "target": "**612"})).status_code == 404        # nur interne Geraete
        voicecmd.save({"devices": "**611"})
        assert (await post({"device": "**610", "target": "**612"})).status_code == 403          # nicht freigegeben
        voicecmd.save({"on": False})
        assert (await post({"device": "**611", "target": "**612"})).status_code == 403          # ausgeschaltet
        assert not [c for c in b.ami.calls if c[0] == "originate"]
    run(bridge, scenario)


def test_voice_assistant_greets_and_connects(bridge):
    bridge.genai.script([("hear", "Verbinde mich mit Mama."),
                         ("tool", "connect_contact", {"name": "Mama", "mitschrift": False}), ("say", "Ich verbinde.")])

    async def scenario(b):
        key = setup(b.db)
        r = await b.http.post("/api/voice/assistant", json={"device": "Lars"}, headers=H(key))
        assert r.status_code == 200, r.text
        await b.settle()
        orig = [c for c in b.ami.calls if c[0] == "originate"][-1]
        assert orig[1] == "**611" and orig[3] == "outbound-ai"
        u = orig[2]
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        s = b.genai.sessions[0]
        assert "SPRACHBEFEHL" in s.prompt and "Hallo Lars" in s.prompt
        names = [f.name for t in (s.config.tools or []) for f in (t.function_declarations or [])]
        assert "connect_contact" in names and "save_result" not in names
        assert "mit wem du sie verbinden sollst" in s.texts[0]
        assert s.tool_results[0][1].get("ok") is True, s.tool_results
        assert (await b.internal("after", uuid=u)).startswith("dial:0241123456")
    run(bridge, scenario)


def test_voice_key_only_hashed(bridge):
    async def scenario(b):
        key = setup(b.db)
        assert key not in str(b.db.q("SELECT value FROM meta"))
        assert voicecmd.settings()["key_hint"] == "…" + key[-4:]
        voicecmd.drop_key()
        r = await b.http.post("/api/voice/assistant", json={"device": "**611"}, headers=H(key))
        assert r.status_code == 403
        assert main.voicecmd is voicecmd
    run(bridge, scenario)


def test_voice_names_states_and_hangup(bridge):
    async def scenario(b):
        key = setup(b.db)
        assert voicecmd.save({"names": "Headset 1=**610; Küche=**611"}) is None
        for bad in ("Headset=0241", "ohne Gleich", "A=**610; a=**611"):
            try:
                voicecmd.save({"names": bad})
                assert False, bad
            except voicecmd.VoiceError as e:
                assert e.status == 400
        r = await b.http.get("/api/voice/info", headers=H(key))
        assert r.status_code == 200 and {"name": "Headset 1", "number": "**610"} in r.json()["devices"]
        tg = {t["value"]: t for t in r.json()["targets"]}
        assert tg["Lars Tester"]["kind"] == "benutzer" and tg["0241123456"]["label"] == "Mama: 0241123456" and tg["0241123456"]["name"] == "Mama"
        q = b.main.hub.subscribe()
        r = await b.http.post("/api/voice/connect", json={"device": "headset 1", "target": "Mama"}, headers=H(key))
        assert r.status_code == 200 and r.json()["device"] == "**610" and r.json()["state"] == "klingelt"
        cid = r.json()["call_id"]
        await b.settle()
        u = [c for c in b.ami.calls if c[0] == "originate"][-1][2]
        # vor dem Abheben: kein Kanal, auflegen geht nicht
        assert (await b.http.post("/api/voice/hangup", json={"call_id": cid}, headers=H(key))).status_code == 409
        await b.internal("vc-event", uuid=u, ev="up", chan="PJSIP/fritz-0000001")
        await b.internal("vc-event", uuid=u, ev="conn")
        r = await b.http.get(f"/api/voice/calls/{cid}", headers=H(key))
        assert r.json()["state"] == "verbunden" and "chan" not in r.json()
        r = await b.http.post("/api/voice/hangup", json={"device": "Headset 1"}, headers=H(key))
        assert r.status_code == 200
        assert ("action", "Hangup", {"Channel": "PJSIP/fritz-0000001"}) in b.ami.calls
        await b.internal("vc-event", uuid=u, ev="end", st="ANSWER")
        states = []
        while not q.empty():
            ev = q.get_nowait()
            if ev.get("type") == "vc":
                states.append(ev["call"]["state"])
        b.main.hub.unsubscribe(q)
        assert states == ["klingelt", "abgenommen", "verbunden", "beendet"], states
        # Ziel nimmt nicht ab: nicht_erreicht mit Grund
        voicecmd._last.clear()
        r = await b.http.post("/api/voice/connect", json={"device": "**611", "target": "Mama"}, headers=H(key))
        await b.settle()
        u = [c for c in b.ami.calls if c[0] == "originate"][-1][2]
        await b.internal("vc-event", uuid=u, ev="up", chan="PJSIP/fritz-0000002")
        await b.internal("vc-event", uuid=u, ev="end", st="NOANSWER")
        s = voicecmd.STATES[r.json()["call_id"]]
        assert s["state"] == "nicht_erreicht" and s["reason"] == "keine Antwort"
        # Telefon selbst nicht erreicht
        voicecmd._last.clear()
        b.ami.originate_ok = (False, "Besetzt")
        r = await b.http.post("/api/voice/connect", json={"device": "**612", "target": "Mama"}, headers=H(key))
        await b.settle()
        s = voicecmd.STATES[r.json()["call_id"]]
        assert s["state"] == "nicht_erreicht" and "Besetzt" in s["reason"]
        assert (await b.http.get("/api/voice/events", headers={"authorization": "Bearer falsch"})).status_code == 401
    run(bridge, scenario)


def test_voice_assistant_states(bridge):
    bridge.genai.script([("hear", "Verbinde mich mit Mama."),
                         ("tool", "connect_contact", {"name": "Mama", "mitschrift": False}), ("say", "Ich verbinde.")])

    async def scenario(b):
        key = setup(b.db)
        r = await b.http.post("/api/voice/assistant", json={"device": "**611"}, headers=H(key))
        assert r.status_code == 200 and r.json()["state"] == "klingelt"
        cid = r.json()["call_id"]
        await b.settle()
        orig = [c for c in b.ami.calls if c[0] == "originate"][-1]
        assert orig[4] == {"VC": "1"}
        u = orig[2]
        await b.internal("vc-event", uuid=u, ev="ki", chan="PJSIP/fritz-0000003")
        assert voicecmd.STATES[cid]["state"] == "ki"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        assert (await b.internal("after", uuid=u)).startswith("dial:0241123456")
        await b.internal("vc-event", uuid=u, ev="conn")
        await b.internal("vc-event", uuid=u, ev="end", st="ANSWER")
        assert voicecmd.STATES[cid]["state"] == "beendet"
        # KI-Anruf ohne Verbindung beendet
        voicecmd._last.clear()
        r = await b.http.post("/api/voice/assistant", json={"device": "**611"}, headers=H(key))
        await b.settle()
        u = [c for c in b.ami.calls if c[0] == "originate"][-1][2]
        await b.internal("vc-event", uuid=u, ev="ki", chan="PJSIP/fritz-0000004")
        await b.internal("vc-event", uuid=u, ev="end", st="")
        s = voicecmd.STATES[r.json()["call_id"]]
        assert s["state"] == "beendet" and s["reason"] == "ohne Verbindung"
    run(bridge, scenario)


def test_ha_integration_download(bridge):
    async def scenario(b):
        voicecmd.save({"on": False})
        assert (await b.http.get("/ha/lieschen.tar.gz")).status_code == 404
        setup(b.db)
        r = await b.http.get("/ha/lieschen.tar.gz")
        assert r.status_code == 200
        import io
        import json
        import tarfile
        t = tarfile.open(fileobj=io.BytesIO(r.content))
        names = t.getnames()
        assert "custom_components/lieschen/manifest.json" in names and "custom_components/lieschen/switch.py" in names
        assert not [n for n in names if "__pycache__" in n or "tests" in n]
        assert json.load(t.extractfile("custom_components/lieschen/manifest.json"))["domain"] == "lieschen"
    run(bridge, scenario)


def test_voice_several_phones(bridge):
    bridge.genai.script([("hear", "Verbinde mich mit Mama."),
                         ("tool", "connect_contact", {"name": "Mama", "mitschrift": False}), ("say", "Ich verbinde.")])

    async def scenario(b):
        from app import ami
        key = setup(b.db)
        b.db.save_staff({"name": "Erna Zweit", "number": "**612", "msn": "", "status": "office"})
        assert voicecmd.save({"names": "Haus=**610 + **611, **612"}) is None
        assert voicecmd.names_cfg() == [("Haus", "**610+**611+**612")]
        voicecmd.save({"names": "Headset 1 = 612, Küche=**613+614\nFlur=**615"})        # Komma als Trenner, Nummern ohne **
        assert voicecmd.names_cfg() == [("Headset 1", "**612"), ("Küche", "**613+**614"), ("Flur", "**615")]
        voicecmd.save({"names": "Haus=**610 + **611, **612"})
        assert voicecmd.device("**611, **610") == "**611+**610"
        for bad in ("**610+0241", "+".join(f"**61{i}" for i in range(7))):
            try:
                voicecmd.save({"names": f"X={bad}"})
                assert False, bad
            except voicecmd.VoiceError as e:
                assert e.status == 400
        # Kanal: mehrere Telefone ueber den Hilfskanal, eines direkt
        assert ami.originate_channel("**610", "u1") == ("PJSIP/**610@fritz", None)
        assert ami.originate_channel("**610+**612", "u1", {"VC": "1"}) == ("Local/610-612@vc-ring/n", {"VC": "1", "__VCUUID": "u1"})
        voicecmd.save({"devices": "**610, **611"})
        r = await b.http.post("/api/voice/assistant", json={"device": "Haus"}, headers=H(key))
        assert r.status_code == 403                                            # **612 nicht freigegeben
        voicecmd.save({"devices": ""})
        r = await b.http.post("/api/voice/assistant", json={"device": "Haus"}, headers=H(key))
        assert r.status_code == 200 and r.json()["device"] == "**610+**611+**612"
        cid = r.json()["call_id"]
        await b.settle()
        orig = [c for c in b.ami.calls if c[0] == "originate"][-1]
        assert orig[1] == "**610+**611+**612"
        u = orig[2]
        assert voicecmd.STATES[cid]["answered"] == ""
        # Erna hebt an **612 ab (Klingel-Rufnummer 99612); danach meldet sich die KI mit ihrem Namen
        await b.internal("vc-event", uuid=u, ev="who", ep="99612", uri="sip:99612@192.168.1.20:5060")
        assert voicecmd.STATES[cid]["answered"] == "**612"
        await b.internal("vc-event", uuid=u, ev="ki", chan="Local/610-611-612@vc-ring-00000001;1")
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        s = b.genai.sessions[0]
        assert "Hallo Erna" in s.prompt and "Telefon **612 abgenommen" in s.prompt
        r = await b.http.post("/api/voice/hangup", json={"call_id": cid}, headers=H(key))
        assert ("action", "Hangup", {"Channel": "Local/610-611-612@vc-ring-00000001;1"}) in b.ami.calls
        assert voicecmd.answered_dev("fritz", "<sip:**611@fritz.box>") == "**611"
    run(bridge, scenario)


def test_agent_needs_name(bridge):
    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: {"role": "admin", "username": "t"}
        n = len(b.db.list_agents())
        r = await b.http.post("/api/agents", json={"name": "  ", "mode": "assist", "voice": "Leda", "speech_pace": "fast"})
        assert r.status_code == 400 and "Namen" in r.json()["detail"]
        assert len(b.db.list_agents()) == n
        r = await b.http.post("/api/agents", json={"name": "Neu", "mode": "assist", "voice": "Leda", "speech_pace": "fast",
                                                    "speech_style": "jung"})
        assert r.status_code == 200
        a = [x for x in b.db.list_agents() if x["name"] == "Neu"][0]
        assert (a["voice"], a["speech_pace"], a["speech_style"]) == ("Leda", "fast", "jung")
    try:
        run(bridge, scenario)
    finally:
        main.app.dependency_overrides.pop(main.auth, None)


def test_voice_nolog(bridge):
    """Option "Gespraech mit der KI nicht mitschreiben": bei Sprachbefehlen nur Systemzeilen, sonst unveraendert."""
    from app import call as call_mod
    db = bridge.db
    voicecmd.save({"nolog": True})
    assert voicecmd.settings()["nolog"] is True
    cid = db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction) VALUES(?,?,?,?,?,?,?)",
               (str(uuidm.uuid4()), 1.0, "x", "y", "Vermittlung", "beendet", "out"))

    def fake(voice):
        c = call_mod.Call.__new__(call_mod.Call)
        c.history, c.agent, c.meta = [], {"name": "Vermittlung"}, {"call_id": cid, **({"voice": {"device": "**611"}} if voice else {})}
        return c

    c = fake(True)
    c.event("agent", "Hallo, mit wem soll ich dich verbinden?")
    c.event("caller", "Max Mobil")
    c.event("system", "Verbinde mit Max (Mobil)")
    rows = db.q("SELECT speaker FROM segments WHERE call_id=?", (cid,))
    assert [r["speaker"] for r in rows] == ["system"]
    assert len(c.history) == 2                       # die KI kennt den Verlauf trotzdem
    fake(False).event("caller", "normaler Anruf")    # ohne Sprachbefehl wird weiter mitgeschrieben
    assert len(db.q("SELECT 1 FROM segments WHERE call_id=?", (cid,))) == 2
    voicecmd.save({"nolog": False})
    fake(True).event("caller", "wieder an")
    assert len(db.q("SELECT 1 FROM segments WHERE call_id=?", (cid,))) == 3


def test_contact_aliases(bridge):
    """Weitere Namen: die KI findet den Kontakt auch darunter; auch fuer Fritzbox-Kontakte setzbar."""
    db = bridge.db
    cid = db.x("INSERT INTO contacts(name,number,note,source) VALUES(?,?,?,?)", ("Tierarztpraxis Pfote", "02419876543", "", "fritz"))
    assert not db.find_contacts_by_name("Petra Kurz")

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: {"role": "admin", "username": "t"}
        r = await b.http.put(f"/api/contacts/{cid}/aliases", json={"aliases": " Petra  Kurz; petra kurz , Tierarzt"})
        assert r.status_code == 200 and r.json()["aliases"] == "Petra Kurz; Tierarzt"
        assert (await b.http.put("/api/contacts/999999/aliases", json={"aliases": "x"})).status_code == 404
    try:
        run(bridge, scenario)
    finally:
        main.app.dependency_overrides.pop(main.auth, None)
    for q in ("Petra Kurz", "Frau Kurtz", "Tierarzt", "Pfote"):
        assert [h["id"] for h in db.find_contacts_by_name(q)] == [cid], q
    assert not db.find_contacts_by_name("Petra Tierarzt")        # Woerter aus verschiedenen Namen mischen sich nicht
    db.save_contact({"name": "Mama", "number": "0241123456", "note": ""})
    m = [c for c in db.list_contacts() if c["name"] == "Mama"][0]
    db.save_contact({"name": "Mama", "number": "0241123456", "note": "neu"}, m["id"])
    assert [c for c in db.list_contacts() if c["id"] == cid][0]["aliases"] == "Petra Kurz; Tierarzt"
