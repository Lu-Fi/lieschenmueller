"""Eingehender Anruf, den die KI annimmt: Hinweis-Ansage, Begruessung, Notiz, Ende (Verhalten festgeschrieben)."""
import json
import uuid

from tests.conftest import run

CALLER = "030123456"


def test_ai_call_notice_greeting_note_end(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script([
        ("say", "Guten Tag, wie kann ich helfen?"),
        ("hear", "Ich moechte Frau Meier eine Nachricht hinterlassen."),
        ("tool", "note_message", {"empfaenger": "Frau Meier", "nachricht": "Bitte zurueckrufen"}),
        ("say", "Ich richte es aus. Auf Wiederhoeren."),
        ("tool", "end_call"),
    ])

    async def scenario(b):
        res = await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:99613@fritz.box>", chan="PJSIP/fritz-00000001")
        assert res == "ok"
        sock = await b.audiosocket(u)
        await sock.audio(10)
        await b.wait_ended(sock)
        assert sock.got_hangup.is_set()               # die Bridge legt mit Hangup-Frame auf (Dialplan laeuft weiter)
        assert await b.internal("after", uuid=u) == "hangup"
        row = b.call_row(u)
        assert row["status"] == "beendet" and row["ended"]
        assert row["agent_name"] == "Assistent"
        assert json.loads(row["notes"]) == {"Nachricht für Frau Meier": "Bitte zurueckrufen"}
        sys_ = b.segments(row["id"], "system")
        assert sys_[0] == "Hinweis-Ansage"
        assert "Gespraech beendet: KI hat aufgelegt" in sys_
        assert b.segments(row["id"], "agent") == ["Guten Tag, wie kann ich helfen?", "Ich richte es aus. Auf Wiederhoeren."]
        assert b.segments(row["id"], "caller") == ["Ich moechte Frau Meier eine Nachricht hinterlassen."]
        s = b.genai.sessions[0]
        assert s.texts and "Begruesse" in s.texts[0]           # Begruessung des Agenten ging an Gemini
        assert "Anrufernummer laut Telefonsystem (verlaesslich): 030123456" in s.prompt
        assert await b.internal("rec-done", uuid=u) == "ok"
        await b.settle()
        assert row["id"] in b.finished                          # Nachbearbeitung lief (Benachrichtigung)
        assert b.analyzed == []                                 # Auswertung nur bei Agenten mit "Zusatzinfos" (meta_on)
    run(bridge, scenario)
