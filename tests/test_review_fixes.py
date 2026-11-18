"""Korrekturen aus dem Review (Paket 1): Kosten addieren, Kostenbremse nur fuer KI-Anrufe, Aufnahme-Ansage fuer Benutzer,
Dateien beim Loeschen, Preise je Textmodell."""
import os
import time
import types
import uuid

from app import call as call_mod, costguard, main, models
from tests.conftest import run


def _usage(text_in=100, audio_in=200, audio_out=300, think=0):
    md = lambda kind, n: types.SimpleNamespace(modality=kind, token_count=n)
    return types.SimpleNamespace(prompt_tokens_details=[md("TEXT", text_in), md("AUDIO", audio_in)],
                                 response_tokens_details=[md("AUDIO", audio_out)], thoughts_token_count=think)


def _fake_call(cid):
    c = call_mod.Call.__new__(call_mod.Call)
    c.tok = {"in_text": 0, "in_audio": 0, "out_text": 0, "out_audio": 0, "think": 0}
    c.cost, c.fail_n, c.agent, c.meta = 0.0, 0, {"model": ""}, {"call_id": cid}
    return c


def _call_row(db, **kw):
    cols = {"uuid": str(uuid.uuid4()), "started": time.time() - 60, "ended": time.time(), "caller": "015112345678",
            "called": "0309999", "agent_name": "Assistent", "status": "beendet", "direction": "in", **kw}
    return db.x(f"INSERT INTO calls({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values()))


def test_usage_is_added_not_overwritten(bridge):
    async def scenario(b):
        cid = _call_row(b.db, cost_usd=0.5, tok_in_audio=1000)      # z. B. Transkript schon abgerechnet
        first, second = _fake_call(cid), _fake_call(cid)             # zwei KI-Sitzungen desselben Anrufs
        first.add_usage(_usage())
        second.add_usage(_usage(audio_out=100))
        r = b.db.q("SELECT * FROM calls WHERE id=?", (cid,))[0]
        assert r["tok_in_audio"] == 1000 + 200 + 200
        assert r["tok_out_audio"] == 300 + 100
        assert abs(r["cost_usd"] - (0.5 + first.cost + second.cost)) < 1e-9
    run(bridge, scenario)


def test_cost_brake_counts_only_ai_calls(bridge):
    async def scenario(b):
        caller = "015199999999"
        _call_row(b.db, caller=caller, cost_usd=0.02, tok_in_audio=5000, tok_out_text=300)    # Mitschrift: nur Transkriptkosten
        assert costguard.usage(caller)[0] == 0
        cid = _call_row(b.db, caller=caller, cost_usd=0.0)
        b.db.add_segment(cid, "agent", "Guten Tag")                                          # KI hat gesprochen
        assert costguard.usage(caller)[0] == 1
        _call_row(b.db, caller=caller, tok_out_audio=50)                                     # KI-Sprachausgabe
        assert costguard.usage(caller)[0] == 2
    run(bridge, scenario)


def test_text_price_per_model():
    assert models.text_price("gemini-3.1-flash-lite") == models.TRANSCRIBE["price"]
    assert models.text_price("gemini-3.8-flash")["in_audio"] == 1.0
    assert models.text_price("unbekannt") == models.DETAIL["price"]


def test_remove_call_deletes_side_files(bridge):
    async def scenario(b):
        base = os.path.dirname(b.db.DB_PATH)
        os.makedirs(os.path.join(base, "rec"), exist_ok=True)
        os.makedirs(os.path.join(base, "backups"), exist_ok=True)
        cid = _call_row(b.db)
        other = _call_row(b.db)
        mk = lambda *p: open(os.path.join(base, *p), "w").close() or os.path.join(base, *p)
        rec = mk("rec", f"call-{cid}.mp3")
        b.db.x("UPDATE calls SET recording=? WHERE id=?", (rec, cid))
        side = [mk("rec", f"call-{cid}-gespraech.mp3"), mk("backups", f"call-{cid}-segments-1.json")]
        keep = [mk("rec", f"call-{other}.mp3"), mk("rec", f"call-{cid}0.mp3")]          # anderer Anruf / gleiche Ziffern am Anfang
        main.remove_call(cid)
        assert not any(os.path.exists(p) for p in [rec, *side])
        assert all(os.path.exists(p) for p in keep)
    run(bridge, scenario)
