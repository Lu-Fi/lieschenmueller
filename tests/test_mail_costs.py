"""Mail mit Aufnahme: Groessenlimit und Rueckfall ohne Audio; Kostenbremse. Verhalten festgeschrieben."""
import json
import os
import time
import uuid

import pytest

from app import notify
from tests.conftest import run

CALLER = "015112345678"


def smtp(db, max_mb=1):
    for k, v in (("smtp_host", "mail.invalid"), ("smtp_user", "test@example.invalid"), ("smtp_password", "x"),
                 ("smtp_from", "test@example.invalid"), ("mail_max_mb", str(max_mb))):
        db.set_meta(k, v)
    db.x("INSERT INTO mail_recipients(email, on_call, enabled, inc_audio) VALUES('empf@example.invalid','all',1,1)")


def call_with_recording(db, size):
    os.makedirs(os.path.join(os.path.dirname(db.DB_PATH), "rec"), exist_ok=True)
    path = os.path.join(os.path.dirname(db.DB_PATH), "rec", "call-x.mp3")
    open(path, "wb").write(b"\x00" * size)
    return db.x("INSERT INTO calls(uuid,started,ended,caller,called,agent_name,status,direction,recording,meta) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), time.time() - 60, time.time(), CALLER, "0301111", "Assistent", "beendet", "in", path,
                 json.dumps({"zusammenfassung": "Test"})))


def test_mail_small_recording_attached(bridge):
    async def scenario(b):
        smtp(b.db)
        cid = call_with_recording(b.db, 200 * 1024)
        await notify.call_finished(cid)
        assert len(b.mails) == 1 and len(b.mails[0]["attachments"]) == 1
        assert b.mails[0]["attachments"][0][0] == f"anruf-{cid}.mp3"
    run(bridge, scenario)


def test_mail_too_big_without_audio_with_note(bridge):
    async def scenario(b):
        smtp(b.db, max_mb=1)
        cid = call_with_recording(b.db, 1536 * 1024)
        await notify.call_finished(cid)
        assert len(b.mails) == 1 and b.mails[0]["attachments"] == []
        assert "Die Aufnahme (1.5 MB) ist größer als die erlaubte Mailgröße (1 MB)" in b.mails[0]["text"]
        await notify.call_finished(cid)
        assert len(b.mails) == 1                                       # nur einmal je Anruf
    run(bridge, scenario)


def test_mail_server_rejects_attachment_retry_without(bridge, monkeypatch):
    async def scenario(b):
        smtp(b.db)
        cid = call_with_recording(b.db, 100 * 1024)
        sent = []

        def send_mail(to, subject, text, html_body=None, attachments=None):
            if attachments:
                raise RuntimeError("552 message size exceeds limit")
            sent.append(text)
        monkeypatch.setattr(notify, "send_mail", send_mail)
        await notify.call_finished(cid)
        assert len(sent) == 1 and "Die Aufnahme konnte nicht angehängt werden (Mail zu groß für den Server)" in sent[0]
    run(bridge, scenario)


@pytest.mark.parametrize("scope", ["global", "agent"])
def test_cost_brake_rejects_second_ai_call(bridge, scope):
    async def scenario(b):
        if scope == "global":
            b.db.set_meta("limit_calls_h", "1")
        else:
            b.db.x("UPDATE agents SET limit_calls_h=1")
        b.db.x("INSERT INTO calls(uuid,started,ended,caller,called,agent_name,status,direction,cost_usd,tok_out_audio) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (str(uuid.uuid4()), time.time() - 120, time.time() - 60, CALLER, "0309999", "Assistent", "beendet", "in", 0.01, 500))
        u = str(uuid.uuid4())
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0309999@fritz.box>") == "no-agent"
        row = b.call_row(u)
        assert row["status"] == "abgelehnt" and row["agent_name"] == "Kostenbremse (Assistent)"
        assert any(t.startswith("Kostenbremse: nicht an die KI") for t in b.segments(row["id"], "system"))
        # interne Nummern werden nie gebremst
        assert await b.internal("call", uuid=str(uuid.uuid4()), frm="**611", to="s", hdr="<sip:0309999@fritz.box>") == "ok"
    run(bridge, scenario)


def test_cost_brake_on_fallback(bridge):
    async def scenario(b):
        b.db.set_meta("limit_calls_h", "1")
        b.db.x("UPDATE call_rules SET ring_s=20")
        b.db.x("INSERT INTO calls(uuid,started,ended,caller,called,agent_name,status,direction,cost_usd,tok_out_audio) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (str(uuid.uuid4()), time.time() - 120, time.time() - 60, CALLER, "0309999", "Assistent", "beendet", "in", 0.01, 500))
        u = str(uuid.uuid4())
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0309999@fritz.box>") == "fdial:20:99613"
        assert await b.internal("fallback", uuid=u) == "no-agent"
        row = b.call_row(u)
        assert row["status"] == "abgelehnt"
        assert any(t.startswith("Kostenbremse: die KI übernimmt nicht") for t in b.segments(row["id"], "system"))
    run(bridge, scenario)
