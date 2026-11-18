"""Test-Doubles fuer die Bridge: Gemini Live (skriptbar), AudioSocket-Client, AMI-Stub, Stimmen (TTS).

Nichts davon braucht Netz oder Schluessel. Die Fakes ersetzen nur die Grenzen der Bridge (Gemini, Asterisk);
die Anruflogik in main.py/call.py laeuft unveraendert.
"""
import asyncio
import io
import uuid as uuidlib
import wave
from types import SimpleNamespace as NS


# --- Gemini Live -------------------------------------------------------------------------------------------------

def _sc(**kw):
    base = dict(interrupted=False, turn_complete=False, input_transcription=None, output_transcription=None, model_turn=None)
    base.update(kw)
    return NS(**base)


def _msg(server_content=None, tool_call=None, usage=None):
    return NS(server_content=server_content, tool_call=tool_call, usage_metadata=usage)


class FakeSession:
    """Eine Gemini-Live-Sitzung. Schritte (Liste von Tupeln):
      ("say", text)          KI spricht: Transkript + etwas Audio, danach turn_complete
      ("cut", text)          KI beginnt zu sprechen und wird unterbrochen (interrupted, kein turn_complete)
      ("hear", text)         der Anrufer sagt etwas (Eingangs-Transkript)
      ("tool", name, args)   Werkzeugaufruf der KI; die Antwort der Bridge steht danach in .tool_results
      ("wait", sekunden)
    Die Sitzung beginnt erst, wenn die Bridge den ersten Text (Begruessung/Systemhinweis) geschickt hat."""

    def __init__(self, steps, config, fake):
        self.steps = list(steps)
        self.config = config
        self.fake = fake
        self.texts = []              # alles, was die Bridge als Text geschickt hat (Begruessung, Systemhinweise)
        self.audio_in = 0
        self.tool_results = []       # (name, Antwort)
        self.closed = False
        self._first_text = asyncio.Event()
        self._responses = asyncio.Queue()

    @property
    def prompt(self):
        return self.config.system_instruction or ""

    async def send_realtime_input(self, text=None, audio=None, **_):
        if text is not None:
            self.texts.append(text)
            self._first_text.set()
        if audio is not None:
            self.audio_in += 1

    async def send_tool_response(self, function_responses=()):
        for fr in function_responses:
            self.tool_results.append((fr.name, fr.response))
            self._responses.put_nowait(fr)

    async def receive(self):
        try:
            await asyncio.wait_for(self._first_text.wait(), 15)
        except asyncio.TimeoutError:
            pass
        while self.steps:
            step = self.steps.pop(0)
            kind = step[0]
            if kind == "say":
                yield _msg(_sc(output_transcription=NS(text=step[1]),
                               model_turn=NS(parts=[NS(inline_data=NS(data=b"\x00" * 960))])))
                await asyncio.sleep(0.02)
                yield _msg(_sc(turn_complete=True))
            elif kind == "cut":                 # KI beginnt zu sprechen und wird unterbrochen (Barge-in), ohne turn_complete
                yield _msg(_sc(output_transcription=NS(text=step[1]),
                               model_turn=NS(parts=[NS(inline_data=NS(data=b"\x00" * 960))])))
                await asyncio.sleep(0.02)
                yield _msg(_sc(interrupted=True))
            elif kind == "hear":
                yield _msg(_sc(input_transcription=NS(text=step[1])))
            elif kind == "tool":
                fc = NS(id=uuidlib.uuid4().hex[:8], name=step[1], args=dict(step[2] if len(step) > 2 else {}))
                yield _msg(tool_call=NS(function_calls=[fc]))
                try:
                    await asyncio.wait_for(self._responses.get(), 10)
                except asyncio.TimeoutError:
                    pass
            elif kind == "wait":
                await asyncio.sleep(step[1])
            await asyncio.sleep(0.02)
        self.fake.done.set()
        # Skript zu Ende: die Sitzung bleibt offen (wie Gemini), bis der Anruf endet
        while not self.closed:
            await asyncio.sleep(0.1)


class _Connect:
    def __init__(self, fake, model, config):
        self.fake, self.model, self.config = fake, model, config

    async def __aenter__(self):
        if self.fake.fail_connect > 0:          # Gemini nicht erreichbar (Kontingent, Netz)
            self.fake.fail_connect -= 1
            raise RuntimeError("429 RESOURCE_EXHAUSTED (Fake)")
        steps = self.fake.next_script(self.config)
        s = FakeSession(steps, self.config, self.fake)
        self.fake.sessions.append(s)
        self.session = s
        return s

    async def __aexit__(self, *a):
        self.session.closed = True
        return False


class FakeGenai:
    """Ersetzt google.genai.Client in call.py. Skripte werden der Reihe nach an die Sitzungen vergeben
    (optional mit Bedingung: Teilstring im Prompt). Ohne passendes Skript: leere Sitzung."""

    def __init__(self):
        self.scripts = []            # (Teilstring oder None, Schritte)
        self.sessions = []
        self.fail_connect = 0        # so viele Verbindungsversuche schlagen fehl
        self.done = asyncio.Event()

    def script(self, steps, match=None):
        self.scripts.append((match, list(steps)))
        return self

    def next_script(self, config):
        prompt = config.system_instruction or ""
        for i, (match, steps) in enumerate(self.scripts):
            if match is None or match in prompt:
                self.scripts.pop(i)
                return steps
        return []

    def Client(self, *a, **k):        # noqa: N802 (wie google.genai.Client)
        fake = self
        return NS(aio=NS(live=NS(connect=lambda model=None, config=None: _Connect(fake, model, config))))


def silent_wav(seconds=0.2, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * 2)
    return buf.getvalue()


async def fake_voice_sample(voice, text, model=None, compact=False, hint=""):
    """Ersatz fuer voices.sample (Gemini-TTS): kurze Stille."""
    return silent_wav()


# --- AudioSocket -------------------------------------------------------------------------------------------------

class FakeAudioSocket:
    """Spielt Asterisk: verbindet sich mit dem AudioSocket-Server der Bridge, meldet die Anruf-UUID an,
    schickt Audio und auf Wunsch den Hangup-Frame; sammelt, was die Bridge zurueckschickt."""

    def __init__(self, port, call_uuid):
        self.port = port
        self.uuid = call_uuid
        self.frames = 0
        self.got_hangup = asyncio.Event()
        self.closed = asyncio.Event()

    async def connect(self):
        self.r, self.w = await asyncio.open_connection("127.0.0.1", self.port)
        self.w.write(b"\x01" + (16).to_bytes(2, "big") + uuidlib.UUID(self.uuid).bytes)
        await self.w.drain()
        self._task = asyncio.get_running_loop().create_task(self._read())
        return self

    async def _read(self):
        try:
            while True:
                hdr = await self.r.readexactly(3)
                n = int.from_bytes(hdr[1:], "big")
                if n:
                    await self.r.readexactly(n)
                if hdr[0] == 0x10:
                    self.frames += 1
                elif hdr[0] == 0x00:
                    self.got_hangup.set()
                    break
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            # wie Asterisk: nach dem Hangup-Frame der Bridge die Verbindung schliessen (AudioSocket() endet, Dialplan laeuft weiter)
            try:
                self.w.close()
            except Exception:
                pass
            self.closed.set()

    async def audio(self, frames=5, loud=False):
        pcm = (b"\x00\x40" * 160) if loud else (b"\x00" * 320)
        for _ in range(frames):
            self.w.write(b"\x10" + (320).to_bytes(2, "big") + pcm)
        await self.w.drain()

    async def dtmf(self, digit):
        self.w.write(b"\x03\x00\x01" + digit.encode())
        await self.w.drain()

    async def hangup(self):
        """Der Anrufer legt auf (Hangup-Frame von Asterisk)."""
        try:
            self.w.write(b"\x00\x00\x00")
            await self.w.drain()
        except Exception:
            pass


# --- AMI ---------------------------------------------------------------------------------------------------------

class AmiStub:
    """Ersatz fuer die AMI-Funktionen in ami.py: zeichnet alle Aufrufe auf, waehlt nie wirklich."""

    def __init__(self):
        self.calls = []
        self.redirect_ok = (True, "")
        self.originate_ok = (True, "")
        self.reg_status = "Registered"

    def install(self, monkeypatch, ami):
        stub = self

        async def originate(number, uuid, timeout_s=45, context="outbound-ai", variables=None):
            stub.calls.append(("originate", number, uuid, context, dict(variables or {})))
            return stub.originate_ok

        async def redirect(channel, exten, label, variables=None, context="from-fritz"):
            stub.calls.append(("redirect", channel, exten, label, dict(variables or {})))
            return stub.redirect_ok

        async def action(name, fields=None, list_end=None, timeout_s=10):
            stub.calls.append(("action", name, dict(fields or {})))
            return {"Response": "Success"}, []

        async def command(cmd, timeout_s=15):
            stub.calls.append(("command", cmd))
            return ""

        async def registration_status(name, timeout_s=10):
            stub.calls.append(("registration_status", name))
            return stub.reg_status

        async def play_into(key, sound):
            stub.calls.append(("play_into", key, sound))
            return True, ""

        async def bridged_peer(chan, timeout_s=10):
            return ""

        for n, f in dict(originate=originate, redirect=redirect, action=action, command=command,
                         registration_status=registration_status, play_into=play_into, bridged_peer=bridged_peer).items():
            monkeypatch.setattr(ami, n, f)
        return self

    def commands(self):
        return [c[1] for c in self.calls if c[0] == "command"]
