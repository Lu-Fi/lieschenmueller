"""Aufzeichnung: KI-Aufnahme als Mono-Mix, zweiter KI-Abschnitt und Gespraech (Asterisk-Mix) werden an die erste Aufnahme
angehaengt (mp3.append_audio), /internal/rec-done blockiert nicht (Ablegen im Hintergrund). Verhalten festgeschrieben."""
import asyncio
import os
import time
import uuid
import wave

import miniaudio

from app import main, rectrans
from tests.conftest import run

CALLER = "015112345678"


def rec_rule(db, **kw):
    cols = dict(name="KI-Audio", enabled=1, agent_id=0, audio=0, transcript=0, ai_audio=1, stop_cmd=1, notice=1)
    cols.update(kw)
    db.x(f"INSERT INTO rec_rules({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(cols.values()))


def secs(path):
    info = miniaudio.get_file_info(path)
    return info.duration, info.nchannels


def write_wav(path, seconds, channels=1, frames=True):
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(8000)
        if frames:
            w.writeframes(b"\x10\x00" * int(seconds * 8000) * channels)


def test_ai_recording_mono_with_notice(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Guten Tag."), ("wait", 0.5), ("tool", "end_call")])

    async def scenario(b):
        rec_rule(b.db)
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0309999@fritz.box>") == "ok"
        sock = await b.audiosocket(u)
        await sock.audio(25, loud=True)
        await b.wait_ended(sock)
        row = b.call_row(u)
        sys_ = b.segments(row["id"], "system")
        assert "Aufnahme angekuendigt" in sys_ and "Aufnahme laeuft" in sys_
        assert row["recording"].endswith(f"call-{row['id']}.mp3") and os.path.exists(row["recording"])
        dur, ch = secs(row["recording"])
        assert ch == 1 and dur > 0                                   # Anrufer und KI in EINER Mono-Spur
    run(bridge, scenario)


def test_second_ai_section_is_appended(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Guten Tag."), ("hear", "Petra bitte."), ("tool", "connect_employee", {"name": "Petra"}),
                         ("say", "Ich verbinde.")])
    bridge.genai.script([("say", "Leider nicht erreichbar."), ("wait", 1.0), ("tool", "end_call")])

    async def scenario(b):
        rec_rule(b.db)
        b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": "", "status": "office"})
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0309999@fritz.box>") == "ok"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        first = b.call_row(u)["recording"]
        d1, _ = secs(first)
        assert await b.internal("busy", uuid=u, status="NOANSWER") == "ok"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        row = b.call_row(u)
        assert row["recording"] == first                              # dieselbe Datei, nicht ueberschrieben
        d2, ch = secs(row["recording"])
        assert ch == 1 and d2 > d1 + 1.0                              # zweiter Teil + 1 s Pause angehaengt
        assert not [n for n in os.listdir(os.path.dirname(first)) if "teil2" in n]
    run(bridge, scenario)


def test_conversation_appended_and_rec_done_not_blocking(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Guten Tag."), ("wait", 0.5), ("tool", "end_call")])

    async def scenario(b):
        rec_rule(b.db)
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0309999@fritz.box>") == "ok"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        row = b.call_row(u)
        d1, _ = secs(row["recording"])
        b.db.x("UPDATE calls SET rec_keep=1 WHERE id=?", (row["id"],))
        # Asterisk hat die Mix-Datei angelegt, schliesst sie aber erst NACH der Antwort auf rec-done (h-Erweiterung)
        os.makedirs(rectrans.REC_IN, exist_ok=True)
        mix = rectrans.files(u)["mix"]
        write_wav(mix, 0, frames=False)
        t0 = time.monotonic()
        assert await b.internal("rec-done", uuid=u) == "ok"
        assert time.monotonic() - t0 < 1.0                            # wartet nicht auf die Datei
        write_wav(mix, 3.0, channels=2)                                 # jetzt geschlossen (Stereo wird zu Mono gemischt)
        for _ in range(100):
            await asyncio.sleep(0.1)
            if not os.path.exists(mix):
                break
        await asyncio.sleep(0.3)
        row = b.call_row(u)
        d2, ch = secs(row["recording"])
        assert ch == 1 and d2 > d1 + 3.5                              # Gespraech + 1 s Pause angehaengt
        assert row["recording"].endswith(f"call-{row['id']}.mp3")
        assert not os.path.exists(mix)
    run(bridge, scenario)


def test_transfer_with_record_and_rec_start(bridge):
    """connect_contact mit Mitschrift: rec:<ziel>; rec-start meldet den Beginn (Live-Mitschrift, HUMAN)."""
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Mit wem?"), ("tool", "connect_contact", {"name": "Petra", "mitschrift": True}), ("say", "Ich verbinde.")])

    async def scenario(b):
        os.makedirs(rectrans.SOUNDS, exist_ok=True)
        open(os.path.join(rectrans.SOUNDS, "rec_notice.sln"), "wb").write(b"\x00" * 3200)
        b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": "", "status": "office"})
        b.db.save_staff({"name": "Moritz Intern", "number": "**611", "msn": "", "status": "office"})
        assert await b.internal("call", uuid=u, frm="**611", to="s", hdr="<sip:0309999@fritz.box>") == "ok"   # interner Anrufer darf waehlen
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        assert await b.internal("after", uuid=u) == "rec:**610"
        started = []
        orig = rectrans.start_live
        rectrans.start_live = lambda *a, **k: started.append(a)
        try:
            assert await b.internal("rec-start", uuid=u, chan="PJSIP/fritz-9") == "ok"
        finally:
            rectrans.start_live = orig
        assert main.CALLS.field(u, "human")["rec"] is True and main.CALLS.field(u, "chan")["chan"] == "PJSIP/fritz-9"
        assert b.db.get_meta(f"recbase:{u}", "")
    run(bridge, scenario)
