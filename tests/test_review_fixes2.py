"""Korrekturen aus dem Review (Paket 2): Gemini-Ausfall, Mail erst nach dem ganzen Gespraech, erneut transkribieren,
Mail-Filter nur fuer eingehende Anrufe, Zeitbasis der Aufnahme."""
import os
import time
import uuid

import pytest
from fastapi import HTTPException

from app import call as call_mod, main, notify, rectrans
from tests.conftest import run
from tests.test_transfer import CONNECT, setup_staff, start_ai

CALLER = "030123456"


def test_gemini_unreachable_rings_phones_instead_of_silence(bridge, monkeypatch):
    monkeypatch.setattr(call_mod, "GIVE_UP_FAILS", 1)
    bridge.genai.fail_connect = 5
    u = str(uuid.uuid4())

    async def scenario(b):
        b.db.set_meta("ring_all", "99613")
        await start_ai(b, u)
        after = await b.internal("after", uuid=u)
        assert after.startswith("dial:99613"), after                         # Telefone klingeln statt Stille
        assert any(t.startswith("KI nicht erreichbar") for t in b.segments(b.call_row(u)["id"], "system"))
        assert b.segments(b.call_row(u)["id"], "system").count("Hinweis-Ansage") <= 1
    run(bridge, scenario)


def test_mail_waits_until_transferred_call_ends(bridge):
    bridge.genai.script(CONNECT)
    u = str(uuid.uuid4())

    async def scenario(b):
        for k, v in (("smtp_host", "mail.invalid"), ("smtp_user", "t@example.invalid"), ("smtp_password", "x"), ("smtp_from", "t@example.invalid")):
            b.db.set_meta(k, v)
        b.db.x("INSERT INTO mail_recipients(email,on_call,enabled) VALUES('a@example.invalid','all',1)")
        setup_staff(b.db)
        await start_ai(b, u)
        assert (await b.internal("after", uuid=u)).startswith("dial:**610")
        await __import__("asyncio").sleep(0.3)
        assert b.call_row(u)["notified"] == 0                               # noch keine Mail: das verbundene Gespraech laeuft
        await b.internal("rec-done", uuid=u)
        for _ in range(80):
            if b.call_row(u)["notified"]:
                break
            await __import__("asyncio").sleep(0.1)
        assert b.call_row(u)["notified"] == 1
    run(bridge, scenario)


def test_retranscribe_only_with_raw_tracks(bridge, monkeypatch):
    async def scenario(b):
        started = []

        async def fake(cid, uid, direction, notify_end=None):
            started.append((cid, direction))
        monkeypatch.setattr(main, "transcribe_then_notify", fake)
        u = str(uuid.uuid4())
        cid = b.db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction) VALUES(?,?,?,?,?,?,?)",
                     (u, time.time(), CALLER, "0309999", "x", "transkription fehlgeschlagen", "in"))
        with pytest.raises(HTTPException) as e:
            await main.retranscribe(cid)                                     # keine Rohspuren mehr: nichts ueberschreiben
        assert e.value.status_code == 409
        os.makedirs(rectrans.REC_IN, exist_ok=True)
        open(rectrans.files(u)["local"], "wb").close()
        await main.retranscribe(cid)
        await __import__("asyncio").sleep(0)
        assert started == [(cid, "in")]
        b.db.x("UPDATE calls SET status='aktiv' WHERE id=?", (cid,))
        with pytest.raises(HTTPException):
            await main.retranscribe(cid)
        out = b.db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction) VALUES(?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), time.time(), CALLER, "0309999", "x", "beendet", "out"))
        with pytest.raises(HTTPException) as e:
            await main.retranscribe(out)
        assert e.value.status_code == 404
    run(bridge, scenario)


def test_status_filter_only_for_incoming_and_failed_status_mapped(bridge):
    async def scenario(b):
        for k, v in (("smtp_host", "mail.invalid"), ("smtp_user", "t@example.invalid"), ("smtp_password", "x"), ("smtp_from", "t@example.invalid")):
            b.db.set_meta(k, v)
        b.db.x("INSERT INTO mail_recipients(email,on_call,enabled,on_status,on_rec) VALUES('r@example.invalid','all',1,'unbeantwortet',1)")
        rec = b.db.x("INSERT INTO calls(uuid,started,ended,caller,called,agent_name,status,direction,meta) VALUES(?,?,?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), time.time() - 60, time.time(), "**610", "0301234", "Mitschrift", "beendet", "rec", '{"zusammenfassung":"x"}'))
        await notify.call_finished(rec)
        assert len(b.mails) == 1                                             # Mitschrift: Ergebnis-Filter gilt nicht
        b.mails.clear()
        b.db.x("UPDATE mail_recipients SET on_status='unbeantwortet'")
        inc = b.db.x("INSERT INTO calls(uuid,started,ended,caller,called,agent_name,status,direction,meta) VALUES(?,?,?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), time.time() - 60, time.time(), CALLER, "0309999", "x", "transkription fehlgeschlagen", "in", '{"zusammenfassung":"x"}'))
        await notify.call_finished(inc)
        assert len(b.mails) == 1                                             # niemand hat abgenommen -> zaehlt als "nicht angenommen"
    run(bridge, scenario)


def test_rec_start_sets_time_base_once(bridge):
    async def scenario(b):
        u = str(uuid.uuid4())
        b.db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction,rec_transcribe,rec_voicestop) VALUES(?,?,?,?,?,?,?,0,0)",
               (u, time.time() - 100, CALLER, "0309999", "x", "aktiv", "in"))
        await b.internal("rec-start", uuid=u)
        first = float(b.db.get_meta(f"recbase:{u}", "0"))
        assert time.time() - first < 5                                       # Abnehmen, nicht Anrufbeginn (vor 100 s)
        await b.internal("rec-start", uuid=u)
        assert float(b.db.get_meta(f"recbase:{u}", "0")) == first
    run(bridge, scenario)
