"""Gemeinsame Test-Umgebung: temporaere Datenbank und Datenordner, Fake-Gemini, AMI-Stub, Fake-Stimmen.

Laeuft im Container (tests/run.sh). Die Umgebungsvariablen muessen VOR dem Import der App gesetzt sein, weil einige Module
Pfade und Schalter beim Import lesen (DB_PATH, AMI_SECRET, ...)."""
import asyncio
import os
import shutil
import sys
import tempfile

DATA = os.environ.setdefault("LM_TEST_DATA", tempfile.mkdtemp(prefix="lmtest-"))
os.environ["DB_PATH"] = os.path.join(DATA, "lieschen.db")
os.environ.setdefault("AMI_SECRET", "test-ami-secret")  # nur fuer den abgeleiteten /internal-Schluessel (Integrationstest: eigener Wert)
os.environ.pop("GEMINI_API_KEY", None)                 # nie ein echter Schluessel
for k in ("FRITZ_USER", "FRITZ_PASSWORD", "FRITZ_HOST", "FRITZ_TR064_USER", "FRITZ_TR064_PASSWORD", "SMTP_PASSWORD"):
    os.environ.pop(k, None)
os.environ.setdefault("TZ", "Europe/Berlin")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "bridge"), ROOT]

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import ami, analyze, call as call_mod, db, main, notify, voices  # noqa: E402
from tests.fakes import AmiStub, FakeAudioSocket, FakeGenai, fake_voice_sample  # noqa: E402


def _wipe():
    if db._conn is not None:
        try:
            db._conn.close()
        except Exception:
            pass
        db._conn = None
    for n in os.listdir(DATA):
        p = os.path.join(DATA, n)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)


class Bridge:
    """Helfer fuer ein Szenario: /internal/*-Aufrufe wie der Dialplan, AudioSocket wie Asterisk, Fake-Gemini."""

    def __init__(self, genai, ami_stub, mails, analyzed):
        self.main, self.db = main, db
        self.genai, self.ami, self.mails, self.analyzed = genai, ami_stub, mails, analyzed
        self.port = None
        self.http = None

    async def start(self):
        self.srv = await asyncio.start_server(main.audiosocket, "127.0.0.1", 0)
        self.port = self.srv.sockets[0].getsockname()[1]
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 40000)),
                                      base_url="http://127.0.0.1:8080")

    async def stop(self):
        await self.http.aclose()
        self.srv.close()

    async def internal(self, path, **params):
        """Aufruf wie per CURL aus dem Dialplan; Antwort als Text."""
        if "frm" in params:
            params["from"] = params.pop("frm")
        r = await self.http.get(f"/internal/{path}", params=dict(params, k=main.INTERNAL_KEY))
        assert r.status_code == 200, (path, r.status_code, r.text)
        return r.text

    async def audiosocket(self, call_uuid):
        return await FakeAudioSocket(self.port, call_uuid).connect()

    async def wait_ended(self, sock, timeout=20):
        """Bis die Bridge das Gespraech beendet hat (Hangup-Frame) und der Nachlauf (AFTER, Zeile) geschrieben ist."""
        await asyncio.wait_for(sock.closed.wait(), timeout)
        for _ in range(100):
            if not any(c.meta.get("uuid") == sock.uuid for c in main.active_calls()):
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.05)

    def call_row(self, call_uuid):
        r = db.q("SELECT * FROM calls WHERE uuid=?", (call_uuid,))
        return r[0] if r else None

    def segments(self, cid, speaker=None):
        rows = db.q("SELECT speaker, text FROM segments WHERE call_id=? ORDER BY id", (cid,))
        return [r["text"] for r in rows if speaker is None or r["speaker"] == speaker]

    async def settle(self, seconds=0.2):
        """Hintergrundaufgaben (Auswertung, Mail, Aufnahme ablegen) kurz laufen lassen."""
        await asyncio.sleep(seconds)
        for t in list(main.BG):
            try:
                await asyncio.wait_for(asyncio.shield(t), 10)
            except Exception:
                pass


@pytest.fixture
def bridge(monkeypatch):
    _wipe()
    db.conn()
    main.CALLS.clear()
    main.SPY.clear()
    notify.SENT.clear()
    genai = FakeGenai()
    monkeypatch.setattr(call_mod, "genai", genai)
    monkeypatch.setattr(voices, "sample", fake_voice_sample)
    stub = AmiStub().install(monkeypatch, ami)
    analyzed = []

    async def run_safe(cid):
        analyzed.append(cid)
    monkeypatch.setattr(analyze, "run_safe", run_safe)

    async def analyze_run(cid):           # nie das Textmodell (Gemini) aufrufen
        analyzed.append(cid)
        return None
    monkeypatch.setattr(analyze, "run", analyze_run)
    finished = []
    orig_finished = notify.call_finished

    async def call_finished(cid):
        finished.append(cid)
        await orig_finished(cid)
    monkeypatch.setattr(notify, "call_finished", call_finished)
    mails = []

    def send_mail(to, subject, text, html_body=None, attachments=None):
        mails.append({"to": to, "subject": subject, "text": text, "attachments": list(attachments or [])})
    monkeypatch.setattr(notify, "send_mail", send_mail)
    b = Bridge(genai, stub, mails, analyzed)
    b.finished = finished
    yield b
    _wipe()


# Bekannte Fehler im Stand vor dem Umbau, die in Hintergrundaufgaben auftreten (werden in eigenen Commits behoben)
KNOWN_LOOP_ERRORS = ()


def run(bridge, scenario, timeout=60):
    """Szenario (async def f(b)) in einer frischen Ereignisschleife ausfuehren. Unbehandelte Fehler in Hintergrundaufgaben
    (asyncio: 'Task exception was never retrieved') lassen den Test scheitern."""
    errors = []

    async def go():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda l, ctx: errors.append(ctx))
        bridge.genai.done = asyncio.Event()
        await bridge.start()
        try:
            await asyncio.wait_for(scenario(bridge), timeout)
        finally:
            await bridge.stop()
    asyncio.run(go())
    import gc
    gc.collect()                       # nie abgeholte Fehler von Tasks werden beim Aufraeumen gemeldet
    real = [e for e in errors if not any(k in repr(e.get("exception")) for k in KNOWN_LOOP_ERRORS)]
    assert not real, f"Fehler in Hintergrundaufgaben: {[repr(e.get('exception') or e.get('message')) for e in real]}"
