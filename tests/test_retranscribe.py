"""Transkript aus der Aufnahme neu erstellen (rectrans.retranscribe_from_recording) und die Teilung bei auffaellig wenig Text."""
import json
import os
import types

import pytest

from app import rectrans
from tests.conftest import run
from tests.test_detail import make_call

SR = 16000          # Bytes je Sekunde (8 kHz, 16 Bit)


class FakeClient:
    """Liefert je Anfrage einen vorgegebenen Text; zaehlt die Anfragen."""
    def __init__(self, texts):
        self.texts, self.n = list(texts), 0
        self.aio = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=self._gen))

    async def _gen(self, model, contents):
        t = self.texts[min(self.n, len(self.texts) - 1)]
        self.n += 1
        return types.SimpleNamespace(text=t, usage_metadata=None)


def _loud(seconds):
    return (b"\x00\x20" * 8000) * seconds          # Sinus-aehnlich laut (Pegel > 400), 8 kHz


def _setup(b, monkeypatch, tmp_path, texts, secs=30):
    mp3 = tmp_path / "x.mp3"
    mp3.write_bytes(b"x")
    cid = make_call(b.db, {})
    b.db.x("DELETE FROM segments WHERE call_id=?", (cid,))
    b.db.x("UPDATE calls SET recording=? WHERE id=?", (str(mp3), cid))
    for i in range(3):
        b.db.add_segment(cid, "local", f"alt {i}")
    fake = FakeClient(texts)
    monkeypatch.setattr(rectrans.genai, "Client", lambda: fake)
    monkeypatch.setattr(rectrans.mp3, "mp3_to_pcm8k", lambda p: _loud(secs))
    return cid, fake


def test_replaces_when_more_text_and_backs_up(bridge, monkeypatch, tmp_path):
    async def scenario(b):
        text = "\n".join(f"[00:{i:02d}] das ist ein ziemlich langer satz mit vielen woertern {i}" for i in range(20))
        cid, fake = _setup(b, monkeypatch, tmp_path, [text])
        old_w, new_w, done = await rectrans.retranscribe_from_recording(cid, backup_dir=str(tmp_path))
        assert done and new_w > old_w
        rows = b.db.q("SELECT speaker, text FROM segments WHERE call_id=? ORDER BY ts", (cid,))
        assert {r["speaker"] for r in rows} == {"mix"} and len(rows) == 20
        saved = [f for f in os.listdir(tmp_path) if f.endswith(".json")]
        assert len(saved) == 1 and len(json.load(open(tmp_path / saved[0]))) == 3      # altes Transkript gesichert
    run(bridge, scenario)


def test_keeps_old_when_new_has_less_text(bridge, monkeypatch, tmp_path):
    async def scenario(b):
        cid, fake = _setup(b, monkeypatch, tmp_path, ["[00:01] ja"])
        old_w, new_w, done = await rectrans.retranscribe_from_recording(cid, backup_dir=str(tmp_path))
        assert not done and [r["speaker"] for r in b.db.q("SELECT speaker FROM segments WHERE call_id=?", (cid,))] == ["local"] * 3
        assert not os.listdir(tmp_path) or all(not f.endswith(".json") for f in os.listdir(tmp_path))
    run(bridge, scenario)


def test_refuses_calls_with_ai_part(bridge, monkeypatch, tmp_path):
    async def scenario(b):
        cid, fake = _setup(b, monkeypatch, tmp_path, ["[00:01] x"])
        b.db.add_segment(cid, "agent", "Guten Tag")
        with pytest.raises(ValueError):
            await rectrans.retranscribe_from_recording(cid, backup_dir=str(tmp_path))
    run(bridge, scenario)


def test_sparse_piece_is_split_and_better_half_result_used(bridge, monkeypatch, tmp_path):
    async def scenario(b):
        more = "\n".join(f"[00:{i:02d}] genug text hier damit es viele woerter sind {i}" for i in range(10))
        # 1. Anfrage (ganzes Stueck): fast nichts; Anfragen der Haelften: viel Text
        fake = FakeClient(["[00:01] mhm", more, more])
        client_pcm = _loud(60)
        tok = {"in_text": 0, "in_audio": 0, "out_text": 0}
        lines = await rectrans._transcribe_piece(fake, client_pcm, 0.0, "mix", tok)
        assert fake.n == 3 and len(lines) == 20                    # geteilt, die Haelften ersetzen die duenne Fassung
        starts = sorted({round(t) for t, _, _ in lines})
        assert max(starts) > 30                                   # zweite Haelfte mit richtigem Zeitversatz
    run(bridge, scenario)


def test_transcribe_model_choice(bridge):
    from app import models

    async def scenario(b):
        assert models.transcribe_model("")[0] == "gemini-3.1-flash-lite"
        assert models.transcribe_model("gibts-nicht")[0] == "gemini-3.1-flash-lite"
        assert models.transcribe_model("gemini-3.8-flash")[1]["in_audio"] == 1.0
        b.db.set_meta("transcribe_model", "gemini-3.8-flash")
        seen = []

        async def gen(model, contents):
            seen.append(model)
            return types.SimpleNamespace(text="[keine Sprache]", usage_metadata=None)
        client = types.SimpleNamespace(aio=types.SimpleNamespace(models=types.SimpleNamespace(generate_content=gen)))
        await rectrans._ask(client, b"x")
        assert seen == ["gemini-3.8-flash"]
    run(bridge, scenario)


def test_piece_no_speech_marker_drops_only_its_line_and_caps_invented_text():
    import asyncio
    import struct
    import math
    from app import rectrans
    tone = b"".join(struct.pack("<h", int(3000 * math.sin(i / 3))) for i in range(8000 * 10))      # 10 s "Sprache"
    answers = ["[00:01] Guten Tag.\n[00:03] [keine Sprache]\n[00:05] Wie geht es?"]

    async def ask(client, wav, prompt=None):
        return answers.pop(0), None
    orig = rectrans._ask
    rectrans._ask = ask
    try:
        tok = {"in_text": 0, "in_audio": 0, "out_text": 0}
        lines = asyncio.run(rectrans._transcribe_piece(None, tone, 0, "local", tok))
        assert [t for _, _, t in lines] == ["Guten Tag.", "Wie geht es?"]          # echte Zeilen bleiben
        answers.append("\n".join(f"[00:{i:02d}] " + f"wort{i} " * 30 for i in range(10)))   # 300 Woerter in 30 s: nicht sprechbar
        answers += ["[00:01] Kurz.", "[00:01] Auch kurz."]                                     # Teilung liefert die plausible Fassung
        long_tone = tone * 3
        lines = asyncio.run(rectrans._transcribe_piece(None, long_tone, 0, "local", tok))
        assert [t for _, _, t in lines] == ["Kurz.", "Auch kurz."]
    finally:
        rectrans._ask = orig
