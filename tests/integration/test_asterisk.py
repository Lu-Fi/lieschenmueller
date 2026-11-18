"""Integrationstest gegen eine echte lokale Asterisk-Instanz (nur ueber tests/integration/run_asterisk.sh, Pod ohne Netz).

Die Bridge laeuft hier im Testprozess (HTTP 127.0.0.1:18080, AudioSocket 127.0.0.1:9092) mit Fake-Gemini; Asterisk ruft sie
per CURL (/internal/*) und AudioSocket wie im Betrieb. Anrufe entstehen per AMI Originate ueber Local-Kanaele in [from-fritz];
gewaehlte Ziele (PJSIP/<nr>@fritz) landen bei der Gegenstelle im Pod (tests/integration/callee)."""
import asyncio
import os
import time

import pytest
import uvicorn
from fastapi import Request
from fastapi.responses import PlainTextResponse

from app import ami, analyze, call as call_mod, db, main, notify, rectrans, voices
from tests.fakes import FakeGenai, fake_voice_sample

pytestmark = pytest.mark.skipif(os.environ.get("LM_INTEG") != "1", reason="nur mit tests/run.sh --asterisk")

HITS = []           # (Pfad, Parameter, Antwort) aller /internal/*-Aufrufe des Dialplans
PROBE = {}
FAIL = set()        # /internal/<Pfad>, bei dem die Bridge "ausfaellt" (500, leere Antwort)


@main.app.middleware("http")
async def _record(request: Request, call_next):
    if request.url.path.startswith("/internal/") and request.url.path[len("/internal/"):] in FAIL:
        HITS.append((request.url.path[len("/internal/"):], dict(request.query_params), ""))
        return PlainTextResponse("", status_code=500)
    resp = await call_next(request)
    if request.url.path.startswith("/internal/"):
        body = b""
        async for chunk in resp.body_iterator:
            body += chunk
        HITS.append((request.url.path[len("/internal/"):], dict(request.query_params), body.decode()))
        return PlainTextResponse(body.decode(), status_code=resp.status_code)
    return resp


async def _json_probe():
    return PlainTextResponse('{"route":"fdial","targets":"**610+**620","ring":25,"flags":"qf","moh":"m_ab12cd34"}')


async def _json_echo(request: Request):
    PROBE.update(request.query_params)
    return PlainTextResponse("ok")


main.app.add_api_route("/internal/json-probe", _json_probe)
main.app.add_api_route("/internal/json-echo", _json_echo)


def hits(u, qos=False):
    """Aufrufe des Dialplans fuer einen Anruf; die Leitungswerte beim Auflegen (qos, Reihenfolge der Kanaele zufaellig) nur auf Wunsch."""
    return [(p, r) for p, q, r in HITS if q.get("uuid") == u and (qos or p != "qos")]


def qos_hits(u):
    return [q for p, q, r in HITS if p == "qos" and q.get("uuid") == u]


async def wait_for(cond, timeout=60, step=0.2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        await asyncio.sleep(step)
    raise AssertionError("Zeitueberschreitung")


async def ami_ready():
    async def ok():
        try:
            return "System uptime" in await ami.command("core show uptime")
        except Exception:
            return False
    for _ in range(60):
        if await ok():
            return
        await asyncio.sleep(1)
    raise AssertionError("Asterisk/AMI nicht bereit")


async def originate(exten, caller="015112345678"):
    resp, _ = await ami.action("Originate", {"Channel": f"Local/{exten}@from-fritz", "Application": "MusicOnHold", "Data": "default,90",      # erzeugt laufend Audio (AudioSocket bricht nach 2 s Stille ab)
                                             "CallerID": f'"Test" <{caller}>', "Async": "true", "Timeout": "30000"})
    assert resp.get("Response") == "Success", resp
    return await wait_for(lambda: next((q["uuid"] for p, q, r in HITS if p == "call" and q.get("from") == caller
                                         and q["uuid"] not in SEEN and not SEEN.add(q["uuid"])), None), 20)


SEEN = set()


@pytest.fixture
def live(monkeypatch):
    if db._conn is not None:
        db._conn.close()
        db._conn = None
    for n in os.listdir(os.path.dirname(db.DB_PATH)):
        if n.startswith("lieschen.db"):
            os.remove(os.path.join(os.path.dirname(db.DB_PATH), n))
    db.conn()
    HITS.clear()
    genai = FakeGenai()
    monkeypatch.setattr(call_mod, "genai", genai)
    monkeypatch.setattr(voices, "sample", fake_voice_sample)

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(analyze, "run_safe", nothing)
    monkeypatch.setattr(analyze, "run", nothing)
    monkeypatch.setattr(notify, "send_mail", lambda *a, **k: None)
    return genai


def run_live(scenario, timeout=120):
    async def go():
        srv = await asyncio.start_server(main.audiosocket, "127.0.0.1", 9092)
        web = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=18080, log_level="warning"))
        task = asyncio.create_task(web.serve())
        await wait_for(lambda: web.started, 10, 0.05)
        try:
            await ami_ready()
            await rectrans.ensure_notice()                 # Hinweis-Ansage (Fake-TTS) fuer Playback im Dialplan
            await asyncio.wait_for(scenario(), timeout)
        finally:
            web.should_exit = True
            await task
            srv.close()
    asyncio.run(go())


def test_dialplan_reload_clean_and_modules(live):
    async def scenario():
        out = await ami.command("dialplan reload")
        assert "Dialplan reloaded" in out, out
        show = await ami.command("dialplan show from-fritz")
        assert "AudioSocket" in show and "/internal/call" in show
        for ctx in ("screen", "ringprep", "build-targets", "recxstart", "recdstart", "answered", "outbound-ai", "rec-bridge"):
            assert f"'{ctx}'" in await ami.command(f"dialplan show {ctx}"), ctx
        mods = await ami.command("module show like func_json")
        assert "func_json.so" in mods
    run_live(scenario)


def test_json_decode_available_in_dialplan(live):
    """Kann der Dialplan JSON lesen (Paket 2)? Antwort der Bridge per CURL holen und mit JSON_DECODE zerlegen."""
    async def scenario():
        PROBE.clear()
        resp, _ = await ami.action("Originate", {"Channel": "Local/s@lm-json-probe", "Application": "Wait", "Data": "1", "Async": "true"})
        assert resp.get("Response") == "Success"
        await wait_for(lambda: PROBE, 20)
        assert PROBE == {"t": "**610+**620", "g": "25", "f": "qf", "m": "m_ab12cd34"}
    run_live(scenario)


def test_ai_call_full_protocol(live):
    live.script([("say", "Guten Tag, Sie sprechen mit dem Assistenten."), ("wait", 0.5), ("tool", "end_call")])

    async def scenario():
        u = await originate("0309999")
        await wait_for(lambda: any(p == "rec-done" for p, _ in hits(u)))
        assert [p for p, _ in hits(u)] == ["call", "after", "rec-done"]
        assert dict(hits(u))["call"] == "ok" and dict(hits(u))["after"] == "hangup"
        row = db.q("SELECT * FROM calls WHERE uuid=?", (u,))[0]
        assert row["caller"] == "015112345678" and row["called"] == "0309999"
        assert row["status"] == "beendet"
        segs = [r["text"] for r in db.q("SELECT text FROM segments WHERE call_id=? ORDER BY id", (row["id"],))]
        assert "Hinweis-Ansage" in segs and "Guten Tag, Sie sprechen mit dem Assistenten." in segs
    run_live(scenario)


def test_ring_no_answer_then_ai(live):
    live.script([("say", "Hier ist der Anrufbeantworter."), ("tool", "end_call")])

    async def scenario():
        db.x("UPDATE call_rules SET ring_s=5")
        db.save_staff({"name": "Rita Klingel", "number": "**620", "msn": "0301111", "status": "office"})
        u = await originate("0301111")
        await wait_for(lambda: any(p == "rec-done" for p, _ in hits(u)), 60)
        assert [(p, r) for p, r in hits(u)] == [("call", "fdial:5:**620"), ("fallback", "ok"), ("after", "hangup"), ("rec-done", "ok")]
        row = db.q("SELECT * FROM calls WHERE uuid=?", (u,))[0]
        segs = [r["text"] for r in db.q("SELECT text FROM segments WHERE call_id=? ORDER BY id", (row["id"],))]
        assert "Nicht abgenommen: der Anrufbeantworter (KI) übernimmt" in segs
        # angerufenes Telefon (Direktwahl PJSIP/**620@fritz): Ziel und Grund im Ablauf
        q = await wait_for(lambda: [x for x in qos_hits(u) if x.get("leg") == "b"], 10)
        assert q[0].get("to") == "**620" and q[0].get("chan", "").startswith("PJSIP/fritz-"), q
        from app import flow
        ev = [e["text"] for e in flow.list_for(row["id"]) if e["kind"] == "leg_end"]
        assert ev and ev[0].startswith("**620: Klingeln endete"), ev
    run_live(scenario)


def test_ring_answered_by_person(live):
    async def scenario():
        db.x("UPDATE call_rules SET ring_s=10")
        db.save_staff({"name": "Anna Abheben", "number": "**610", "msn": "0302222", "status": "office"})
        u = await originate("0302222")
        await wait_for(lambda: any(p == "answered" for p, _ in hits(u)), 20)
        assert main.CALLS.field(u, "human")
        await wait_for(lambda: any(p == "rec-done" for p, _ in hits(u)), 40)
        assert [p for p, _ in hits(u)] == ["call", "answered", "rec-done"]
        # Leitungswerte des angerufenen Telefons (PJSIP) beim Auflegen; der Anrufer ist hier ein Local-Kanal (keine Werte)
        q = await wait_for(lambda: qos_hits(u), 10)
        assert q[0]["leg"] == "b" and "rxcount=" in q[0]["q"]
        assert not main.CALLS.field(u, "human")
    run_live(scenario)


CONNECT_ASK = [("say", "Guten Tag."), ("hear", "Anna bitte."),
               ("tool", "connect_employee", {"name": "Anna", "anliegen": "Herr Huber wegen der Rechnung"}),
               ("say", "Ich frage kurz nach.")]


def test_screen_accept_connects(live):
    live.script(CONNECT_ASK)
    live.script([("say", "Hallo Anna, Herr Huber wartet. Zeit?"), ("hear", "Ja gerne."), ("tool", "accept")], match="wartender Anrufer")

    async def scenario():
        db.save_staff({"name": "Anna Abheben", "number": "**610", "msn": "", "status": "office"})
        db.x("UPDATE agents SET ask_before=1")
        u = await originate("0309999")
        await wait_for(lambda: any(p == "rec-done" for p, _ in hits(u)), 90)
        seq = hits(u)
        assert [p for p, _ in seq] == ["call", "after", "screen-start", "screen-result", "rec-done"], seq
        assert dict(seq)["after"] == "sdial:**610:25:default:" and dict(seq)["screen-result"] == "accept"
        row = db.q("SELECT * FROM calls WHERE uuid=?", (u,))[0]
        segs = [r["text"] for r in db.q("SELECT text FROM segments WHERE call_id=? ORDER BY id", (row["id"],))]
        assert "Person nimmt den Anruf an" in segs
    run_live(scenario)


def test_screen_decline_back_to_ai_no_reconnect(live):
    live.script(CONNECT_ASK)
    live.script([("say", "Hallo Anna, Zeit?"), ("hear", "Nein, gerade nicht."), ("tool", "decline"), ("say", "In Ordnung.")],
                match="wartender Anrufer")
    live.script([("say", "Anna ist gerade nicht erreichbar. Auf Wiederhoeren."), ("tool", "end_call")])

    async def scenario():
        db.save_staff({"name": "Anna Abheben", "number": "**610", "msn": "", "status": "office"})
        db.x("UPDATE agents SET ask_before=1")
        u = await originate("0309999")
        await wait_for(lambda: any(p == "rec-done" for p, _ in hits(u)), 90)
        seq = hits(u)
        assert [p for p, _ in seq] == ["call", "after", "screen-start", "screen-result", "busy", "after", "rec-done"], seq
        assert seq[3][1] == "decline" and seq[4][1] == "ok"
        assert seq[5][1] == "hangup"                     # kein erneutes Verbinden nach abgelehnter Rueckfrage
        assert [q.get("status") for p, q, r in HITS if p == "busy" and q.get("uuid") == u] == ["ANSWER"]
    run_live(scenario)


def _rms_windows(path, wins):
    import audioop
    import wave
    with wave.open(path) as w:
        rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    return [audioop.rms(pcm[int(a * rate) * 2:int(b * rate) * 2], 2) for a, b in wins]


def test_moh_volume_divisor_attenuates_and_ami_reset_restores():
    """Wartemusik-Zusatzdaempfung wie im Dialplan: VOLUME(TX)=-10 senkt den Pegel um 20 dB, AMI Setvar VOLUME(TX)=0 stellt ihn wieder her (gemessen am Audio)."""
    import random
    import struct
    import wave
    data = os.environ.get("LM_TEST_DATA", "/data")
    d = os.path.join(data, "sounds", "moh", "c", "default")
    rnd = random.Random(1)
    with wave.open(os.path.join(d, "zz-pegeltest.wav"), "wb") as w:         # lautes, gleichmaessiges Rauschen als "Musik"
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", rnd.randint(-9000, 9000)) for _ in range(8000 * 20)))
    os.chmod(os.path.join(d, "zz-pegeltest.wav"), 0o644)

    async def record(exten, reset_at=None):
        name = f"volprobe-{exten}.wav"
        path = os.path.join(data, "rec-in", name)
        if os.path.exists(path):
            os.remove(path)
        resp, _ = await ami.action("Originate", {"Channel": f"Local/{exten}@lm-vol-probe", "Application": "Record", "Data": f"/recordings/{name},0,9,q",
                                                  "Async": "true", "Timeout": "20000"})
        assert resp.get("Response") == "Success", resp
        await asyncio.sleep(reset_at if reset_at else 10)
        if reset_at:
            out = await ami.command("core show channels concise")
            chan = next(l.split("!")[0] for l in out.splitlines() if l.startswith(f"Local/{exten}@lm-vol-probe") and l.split("!")[0].endswith(";2"))
            r, _ = await ami.action("Setvar", {"Channel": chan, "Variable": "VOLUME(TX)", "Value": "0"})
            assert r.get("Response") == "Success", r
            await asyncio.sleep(10 - reset_at)
        await wait_for(lambda: os.path.exists(path) and os.path.getsize(path) > 8000, 15)
        await asyncio.sleep(1)
        return path

    async def scenario():
        await ami_ready()
        await ami.command("moh reload")
        await asyncio.sleep(1)
        base = _rms_windows(await record("k0"), [(2, 4), (6, 8)])
        att = _rms_windows(await record("m10", reset_at=4.5), [(2, 4), (6.5, 8.5)])
        print("PEGEL", base, att)
        assert base[0] > 1000
        assert 0.05 < att[0] / base[0] < 0.2, (base, att)           # -20 dB = Faktor 0,1
        assert att[1] / base[1] > 0.8, (base, att)                  # nach dem Zuruecksetzen wieder voll
    asyncio.run(scenario())


def test_wait_with_music_answers_then_ai_after_delay(live, monkeypatch):
    """Ohne Klingel-Rufnummer und mit Wartemusik: der Dialplan nimmt selbst an, spielt Musik und uebergibt erst nach der Wartezeit an die KI."""
    from app import hold
    monkeypatch.setattr(hold, "pick_class", lambda kind: "default")
    live.script([("say", "Hier ist der Anrufbeantworter."), ("tool", "end_call")])

    async def scenario():
        db.set_meta("ring_all", "")
        db.set_meta("ring_music", "1")
        db.set_meta("ring_delay", "0")
        db.x("UPDATE call_rules SET ring_s=6")
        t0 = time.monotonic()
        u = await originate("0309999")
        await wait_for(lambda: any(p == "rec-done" for p, _ in hits(u)), 60)
        elapsed = time.monotonic() - t0
        call_res = dict(hits(u))["call"]
        assert call_res.startswith("wait:6:default"), call_res
        assert elapsed >= 5.5, elapsed                                    # die KI kam erst nach der Wartezeit (Musik), nicht sofort
        row = db.q("SELECT * FROM calls WHERE uuid=?", (u,))[0]
        segs = [r["text"] for r in db.q("SELECT text FROM segments WHERE call_id=? ORDER BY id", (row["id"],))]
        assert "Hier ist der Anrufbeantworter." in segs
    run_live(scenario)


def test_rec_dial_busy_returns_to_bridge_and_answer_reports_start(live):
    """Durchstellen mit Mitschrift: besetzt -> /internal/busy (Rueckfall zur KI moeglich), kein rec-start; abgehoben -> rec-start erst beim Abheben."""
    async def scenario():
        for target, wait_s in (("630", 15), ("610", 20)):
            resp, _ = await ami.action("Originate", {"Channel": f"Local/r{target}@lm-rec-probe", "Application": "MusicOnHold",
                                                      "Data": "default,25", "Async": "true", "Timeout": "10000"})
            assert resp.get("Response") == "Success", resp
        u_busy, u_ok = "lmtest-rec-630", "lmtest-rec-610"
        await wait_for(lambda: any(p == "busy" and q.get("uuid") == u_busy for p, q, r in HITS), 20)
        await wait_for(lambda: any(p == "rec-start" and q.get("uuid") == u_ok for p, q, r in HITS), 25)
        legs = [q for p, q, r in HITS if q.get("uuid") == u_busy and p == "qos"]
        assert legs and legs[0].get("leg") == "b" and legs[0].get("cause") == "17", legs    # Grund des Endes am Telefon: besetzt
        assert legs[0].get("tech") == "SIP 486 Busy Here" and legs[0].get("to") == "**630", legs   # Ziel auch bei PJSIP/fritz-…
        busy = [(p, q) for p, q, r in HITS if q.get("uuid") == u_busy and p != "qos"]
        assert busy[0][0] == "busy" and busy[0][1].get("status") == "BUSY", busy                 # zuerst die Bridge fragen
        assert "rec-start" not in [p for p, _ in busy], busy                                       # kein rec-start beim Besetzt
        start = next(q for p, q, r in HITS if p == "rec-start" and q.get("uuid") == u_ok)
        assert start.get("chan", "").startswith("Local/r610@lm-rec-probe")                        # Kanal des Anrufers fuer "Aufnahme beenden"
    run_live(scenario)


def test_outbound_ai_transfer_busy_back_to_same_ai(live):
    """Ausgehender KI-Anruf: Durchstellen nutzt denselben Weg wie eingehend (build-targets); besetzt -> dieselbe KI macht weiter."""
    live.script([("say", "Guten Tag, hier Lieschen."), ("hear", "Verbinden Sie mich mit Bernd."),
                 ("tool", "connect_employee", {"name": "Bernd"}), ("say", "Ich verbinde.")])
    live.script([("say", "Bernd ist besetzt. Auf Wiederhoeren."), ("tool", "end_call")])

    async def scenario():
        db.save_staff({"name": "Bernd Besetzt", "number": "**630", "msn": "", "status": "office"})
        agent = next(a for a in db.list_agents(enabled_only=True) if a["mode"] == "assist")
        cid = await main.start_outbound("**610", "Test", "Rueckruf wegen Rechnung", dict(agent, allow_connect=1))
        u = db.q("SELECT uuid FROM calls WHERE id=?", (cid,))[0]["uuid"]
        await wait_for(lambda: any(p == "rec-done" for p, _ in hits(u)), 60)     # **610 legt nach 12 s auf
        seq = hits(u)
        assert seq[0][0] == "after" and seq[0][1].startswith("dial:**630"), seq
        assert seq[1] == ("busy", "ok"), seq                                # Ziel wirklich gewaehlt (besetzt), nicht "PJSIP/dial:..."
        segs = [r["text"] for r in db.q("SELECT text FROM segments WHERE call_id=? ORDER BY id", (cid,))]
        assert "Bernd ist besetzt. Auf Wiederhoeren." in segs, segs
        assert len(live.sessions) == 2 and "Rueckruf wegen Rechnung" in str(live.sessions[1].config)   # derselbe Auftrag
    run_live(scenario)


def test_outcall_passes_sender_and_user_setting_records(live):
    """Ausgehend ueber eine Klingel-Rufnummer (Absender 99NNN): der Dialplan gibt den Absender als from an /internal/outcall weiter;
    hat der Benutzer des Telefons "Ausgehende Gespraeche mitschreiben" an (Telefon umgestellt, meta out_orig), recx ohne Vorwahl."""
    async def scenario():
        from app import outrec
        sid = db.save_staff({"name": "Olga Ausgehend", "number": "**610", "msn": "", "status": "office", "rec_out": "record"})
        outrec._orig_set("**610", "0301111", sid, "DECT")
        db.set_meta("ring_lines", "99610")              # Klingel-Rufnummer eingerichtet (sonst gilt das Telefon nicht als umgestellt)
        seen = set()
        for caller, want in (("99610", "recx:o:0301234567"), ("99611", "dial:0301234567")):
            resp, _ = await ami.action("Originate", {"Channel": "Local/0301234567@from-fritz", "Application": "Wait", "Data": "5",
                                                     "CallerID": f'"Telefon" <{caller}>', "Async": "true", "Timeout": "10000"})
            assert resp.get("Response") == "Success", resp
            q, r = await wait_for(lambda: next(((q, r) for p, q, r in HITS if p == "outcall" and q.get("from") == caller
                                                and q["uuid"] not in seen), None), 20)
            seen.add(q["uuid"])
            assert r == want and q.get("to") == "0301234567", (q, r)
            await wait_for(lambda: any(p == "rec-done" for p, _ in hits(q["uuid"])), 30)
            row = db.q("SELECT * FROM calls WHERE uuid=?", (q["uuid"],))
            if want.startswith("recx"):
                assert row and row[0]["caller"] == "Olga Ausgehend (**610, ausgehend)" and row[0]["owner_staff_id"] == sid
            else:
                assert not row
    run_live(scenario)


def test_outcall_bridge_down_strips_configured_prefix(live):
    """Faellt die Bridge aus (leere Antwort auf /internal/outcall), entfernt der Dialplan selbst die KONFIGURIERTE Vorwahl (Global
    REC_PREFIX aus dyn/globals.conf bzw. per Setvar) und waehlt ohne Aufnahme weiter - frueher nur "000", mit *77 scheiterte der Anruf."""
    async def scenario():
        from app import astconf
        out = await ami.command("dialplan show globals")
        assert "REC_PREFIX=*77" in out, out                    # von render.py angelegt (Voreinstellung)
        db.set_meta("rec_prefix", "#5")                         # Datei ohne Setvar: gilt nach dem Laden des Dialplans
        assert astconf.write_globals()
        await ami.command("dialplan reload")
        assert "REC_PREFIX=#5" in await ami.command("dialplan show globals")
        FAIL.add("outcall")
        try:
            for prefix in ("*77", "*5"):
                db.set_meta("rec_prefix", prefix)
                assert await astconf.apply_globals()            # Setvar ohne Kanal = global, ohne dialplan reload
                assert f"REC_PREFIX={prefix}" in await ami.command("dialplan show globals")
                resp, _ = await ami.action("Originate", {"Channel": f"Local/{prefix}0309876543@from-fritz", "Application": "Wait", "Data": "20",
                                                         "CallerID": '"Telefon" <99610>', "Async": "true", "Timeout": "30000"})
                assert resp.get("Response") == "Success", resp

                async def dialing():
                    for _ in range(100):
                        if "PJSIP/0309876543@fritz" in await ami.command("core show channels concise"):
                            return True
                        await asyncio.sleep(0.2)
                    return False
                assert await dialing(), await ami.command("core show channels concise")
                assert any(p == "outcall" and q.get("to") == f"{prefix}0309876543" for p, q, r in HITS)
                await ami.command("channel request hangup all")
                await asyncio.sleep(1)
        finally:
            FAIL.discard("outcall")
            await ami.command("channel request hangup all")
    run_live(scenario)
