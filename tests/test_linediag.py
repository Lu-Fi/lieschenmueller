"""Fehlersuche bei vermittelten Gespraechen: Seitenpruefung der Einzelspuren (Sprache/Stille/425-Hz-Ton) und Leitungswerte (RTCP)."""
import os
import uuid

import numpy as np

from app import db, flow, linediag, rectrans
from tests.conftest import run

RATE = 8000


def pcm(*parts):
    """parts: (art, Sekunden) mit art = tone (425 Hz), speech (Rauschen mit Silbenrhythmus), silence."""
    out = []
    rng = np.random.default_rng(1)
    for kind, sec in parts:
        t = np.arange(int(sec * RATE)) / RATE
        if kind == "tone":
            x = 8000 * np.sin(2 * np.pi * 425 * t)
        elif kind == "speech":
            x = 3000 * rng.standard_normal(len(t)) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
        else:
            x = np.zeros(len(t))
        out.append(x)
    return np.concatenate(out).astype("<i2").tobytes()


def test_ringback_on_caller_side_is_flagged():
    # wie Testanruf 467: Gegenseite 1 s Ton, 4 s Pause, 1 s Ton; Mitarbeiter spricht
    peer = linediag.analyze(pcm(("silence", 3), ("tone", 1), ("silence", 4), ("tone", 1), ("silence", 1)))
    me = linediag.analyze(pcm(("speech", 2), ("silence", 8)))
    assert peer["tone"] == 2.0 and peer["tone_first"] == 3.0 and peer["speech"] == 0
    assert me["speech"] >= 1.5 and me["tone"] == 0
    w = linediag.warnings({"Gegenseite": peer, "Mitarbeiter": me})
    assert len(w) == 1 and w[0].startswith("Gegenseite: Freizeichen/Besetztton nach dem Abnehmen")
    text = flow.describe("line", {"sides": {"Gegenseite": peer, "Mitarbeiter": me}, "warn": w})
    assert "Gegenseite: 0 s Sprache, 2 s Freizeichen/Besetztton (425 Hz, ab 3 s)" in text and "⚠" in text


def test_silent_side_is_flagged_and_normal_call_is_quiet():
    quiet = linediag.analyze(pcm(("silence", 6)))
    talk = linediag.analyze(pcm(("speech", 3), ("silence", 1), ("speech", 3)))
    assert linediag.warnings({"Gegenseite": quiet}) == ["Gegenseite: keine Sprache – Ton dieser Seite kam evtl. nicht an"]
    assert linediag.warnings({"Gegenseite": talk, "Mitarbeiter": talk}) == []


def test_qos_parse_and_text():
    q = "ssrc=123;themssrc=456;lp=3;rxjitter=0.002;rxcount=0;txjitter=0.001;txcount=512;rlp=1;rtt=0.04;rxmes=0;txmes=90"
    d = linediag.parse_qos(q)
    assert d == {"lp": 3, "rxjitter": 0.002, "rxcount": 0, "txjitter": 0.001, "txcount": 512, "rlp": 1, "rtt": 0.04, "rxmes": 0, "txmes": 90}
    t = linediag.qos_text("a", d)
    assert t.startswith("Leitungswerte eingehende Leitung: 0 Pakete empfangen, 512 gesendet") and "kein Ton an" in t
    assert linediag.parse_qos("") == {} and linediag.parse_qos("kaputt") == {}


def test_qos_endpoint_and_finish_audio_write_flow_events(bridge):
    u = str(uuid.uuid4())

    async def scenario(b):
        cid = db.x("INSERT INTO calls(uuid,started,caller,called,status,direction) VALUES(?,?,?,?,?,?)",
                   (u, 1.0, "015700000002", "5550270", "angenommen", "in"))
        assert await b.internal("qos", uuid=u, leg="b", q="lp=0;rxcount=500;txcount=498;rxjitter=0.001") == "ok"
        assert await b.internal("qos", uuid=u, leg="x", q="rxcount=1") == "ok"           # unbekannte Seite: ignoriert
        assert await b.internal("qos", uuid=u, leg="a", q="") == "ok"                     # leer: nichts
        os.makedirs(rectrans.REC_IN, exist_ok=True)
        f = rectrans.files(u)
        open(f["remote"], "wb").write(rectrans._wav_bytes(pcm(("silence", 3), ("tone", 1), ("silence", 4), ("tone", 1))))
        open(f["local"], "wb").write(rectrans._wav_bytes(pcm(("speech", 2), ("silence", 7))))
        rectrans.finish_audio(cid, u)
        assert not os.path.exists(f["remote"]) and not os.path.exists(f["local"])           # Spuren weiter geloescht
        ev = {e["kind"]: e for e in flow.list_for(cid)}
        assert set(ev) == {"qos", "line"}
        assert ev["qos"]["text"].startswith("Leitungswerte angerufenes Telefon: 500 Pakete empfangen")
        assert "Gegenseite: Freizeichen/Besetztton nach dem Abnehmen" in ev["line"]["text"]
    run(bridge, scenario)


def test_leg_end_reason_and_no_answer_status(bridge):
    """Telefon klingelte, nahm aber nicht ab: Grund und Dauer im Ablauf (Testanruf 17.11.: KI nach 18 statt 60 s)."""
    u = str(uuid.uuid4())

    async def scenario(b):
        cid = db.x("INSERT INTO calls(uuid,started,caller,called,status,direction) VALUES(?,?,?,?,?,?)",
                   (u, 1.0, "021155500001", "5550270", "durchgestellt", "in"))
        flow.add(cid, "ring", targets="**610+**622", ring=60)
        assert await b.internal("qos", uuid=u, leg="b", q="", cause="19", tech="SIP 480 Temporarily Unavailable",
                                chan="PJSIP/99610-0000006e") == "ok"
        assert await b.internal("qos", uuid=u, leg="b", q="", cause="abc", chan="PJSIP/99622-1") == "ok"   # ohne Zahl: nichts
        assert await b.internal("qos", uuid=u, leg="a", q="", cause="16") == "ok"                         # eingehende Seite: nichts
        assert await b.internal("fallback", uuid=u, status="NOANSWER") in ("ok", "no-agent")
        ev = [e for e in flow.list_for(cid) if e["kind"] in ("leg_end", "no_answer")]
        assert [e["kind"] for e in ev] == ["leg_end", "no_answer"]
        assert ev[0]["text"].startswith("**610 (über 99610): Klingeln endete nach ")
        assert "nicht abgenommen, SIP 480 Temporarily Unavailable" in ev[0]["text"]
        assert ev[1]["text"] == "niemand nahm ab (keine Antwort)"
    run(bridge, scenario)


def test_leg_end_after_counts_from_latest_ring_or_transfer(bridge):
    """Durchstellen/Rueckfrage nach dem ersten Klingeln: "nach N s" zaehlt ab dem juengsten ring- bzw. transfer-Ereignis."""
    import time
    u = str(uuid.uuid4())

    async def scenario(b):
        cid = db.x("INSERT INTO calls(uuid,started,caller,called,status,direction) VALUES(?,?,?,?,?,?)",
                   (u, 1.0, "021155500001", "5550270", "durchgestellt", "in"))
        flow.add(cid, "ring", targets="**610", ring=20)
        flow.add(cid, "transfer", targets="**620")
        now = time.time()
        db.x("UPDATE call_events SET ts=? WHERE call_id=? AND kind='ring'", (now - 100, cid))
        db.x("UPDATE call_events SET ts=? WHERE call_id=? AND kind='transfer'", (now - 5, cid))
        assert 4 <= flow.since(u, ("ring", "transfer")) < 20 and flow.since(u, "ring") >= 99
        assert await b.internal("qos", uuid=u, leg="b", q="", cause="19", chan="PJSIP/99620-1") == "ok"
        ev = next(e for e in flow.list_for(cid) if e["kind"] == "leg_end")
        assert 4 <= ev["data"]["after"] < 20, ev["text"]
    run(bridge, scenario)


def test_leg_end_direct_dial_target_from_to(bridge):
    """Direktwahl (PJSIP/fritz-…): das Ziel kommt als to (DIALEDPEERNUMBER ohne @fritz) mit; Klingelzeit abgelaufen = cause 0."""
    u = str(uuid.uuid4())

    async def scenario(b):
        cid = db.x("INSERT INTO calls(uuid,started,caller,called,status,direction) VALUES(?,?,?,?,?,?)",
                   (u, 1.0, "021155500001", "5550270", "durchgestellt", "in"))
        flow.add(cid, "ring", targets="**620+**610", ring=5)
        assert await b.internal("qos", uuid=u, leg="b", q="", cause="0", tech="SIP 180 Ringing", chan="PJSIP/fritz-0000000a",
                                to="**620") == "ok"
        assert await b.internal("qos", uuid=u, leg="b", q="", cause="0", chan="PJSIP/99610-0000000b", to="99610") == "ok"
        assert await b.internal("qos", uuid=u, leg="b", q="", cause="0", chan="PJSIP/fritz-0000000c", to="x;y") == "ok"
        ev = [e["text"] for e in flow.list_for(cid) if e["kind"] == "leg_end"]
        assert ev[0].startswith("**620: Klingeln endete") and "Klingelzeit abgelaufen" in ev[0], ev
        assert ev[1].startswith("**610 (über 99610): Klingeln endete"), ev
        assert ev[2].startswith("Telefon: Klingeln endete"), ev                      # ungueltiges to: kein Ziel
    run(bridge, scenario)

