import asyncio
import hashlib
import io
import os
import time
import wave

from google import genai
from google.genai import types

from . import db, gclient, models
CACHE = os.path.join(os.path.dirname(os.environ.get("DB_PATH", "/data/lieschen.db")), "voices")

VOICES = {
    "Zephyr": "hell", "Puck": "lebhaft", "Charon": "sachlich", "Kore": "bestimmt",
    "Fenrir": "begeistert", "Leda": "jugendlich", "Orus": "bestimmt", "Aoede": "luftig",
    "Callirrhoe": "entspannt", "Autonoe": "hell", "Enceladus": "hauchig", "Iapetus": "klar",
    "Umbriel": "gelassen", "Algieba": "weich", "Despina": "weich", "Erinome": "klar",
    "Algenib": "rau", "Rasalgethi": "sachlich", "Laomedeia": "lebhaft", "Achernar": "sanft",
    "Alnilam": "bestimmt", "Schedar": "gleichmaessig", "Gacrux": "reif", "Pulcherrima": "direkt",
    "Achird": "freundlich", "Zubenelgenubi": "locker", "Vindemiatrix": "sanft",
    "Sadachbia": "munter", "Sadaltager": "kenntnisreich", "Sulafat": "warm",
}


PACES = {"slow": "Sprich ruhig und etwas langsamer als gewoehnlich, mit kurzen Pausen zwischen den Saetzen.",
         "fast": "Sprich zuegig und gut verstaendlich, ohne unnoetige Pausen."}


def speech_hint(pace="", style=""):
    """Sprechanweisung fuer den Prompt aus Tempo (slow/fast) und freiem Stil; leer, wenn nichts eingestellt ist."""
    import re
    parts = []
    if pace in PACES:
        parts.append(PACES[pace])
    st = re.sub(r"[\r\n\t<>\[\]{}\"]+", " ", str(style or "")).strip()[:160]
    if st:
        parts.append(f"Sprechstil: {st}.")
    return " ".join(parts)


def compact_pcm(pcm, sr=24000):
    """Lange Sprechpausen (> 0,15 s) auf 0,1 s kuerzen: feste Ansagen wirken zuegiger."""
    import audioop
    step = int(sr * 0.05) * 2
    out, quiet = bytearray(), 0
    for i in range(0, len(pcm), step):
        chunk = pcm[i:i + step]
        if audioop.rms(chunk, 2) <= 400:
            quiet += 1
            if quiet > 3:          # ab der 4. stillen Teilspur (0,15 s) verwerfen
                continue
        else:
            quiet = 0
        out += chunk
    return bytes(out)


async def sample(voice, text, model=None, compact=False, hint=""):
    """WAV (24 kHz mono) der Stimme ueber die Live-API; gecacht."""
    if voice not in VOICES:
        raise ValueError("unbekannte Stimme")
    os.makedirs(CACHE, exist_ok=True)
    model = models.resolve(model)
    key = hashlib.sha1(f"v4|{compact}|{model}|{voice}|{hint}|{text}".encode()).hexdigest()
    path = os.path.join(CACHE, key + ".wav")
    if os.path.exists(path):
        try:
            os.utime(path)          # zuletzt benutzt: das Aufraeumen (main.housekeeping) loescht zuerst lange nicht benutzte
        except OSError:
            pass
        return open(path, "rb").read()
    cfg = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction="Sprich ausschliesslich den Text, den der Nutzer sendet, woertlich vor. "
                           "Nichts hinzufuegen, keine Rueckfragen. " + (hint or "Sprich zuegig, klar und ohne lange Pausen."),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
    )
    pcm = bytearray()
    tok = dict(in_text=0, in_audio=0, out_text=0, out_audio=0, think=0)

    def count(u):          # Ansagen/Stimmproben kosten auch Tokens, gehoeren aber zu keinem Anruf
        for details, tk, ak in ((u.prompt_tokens_details, "in_text", "in_audio"), (u.response_tokens_details, "out_text", "out_audio")):
            for d in details or []:
                name = str(getattr(d.modality, "name", d.modality)).upper()
                tok[ak if "AUDIO" in name else tk] += d.token_count or 0
        tok["think"] += u.thoughts_token_count or 0

    try:
        await _tts(model, cfg, text, pcm, count)
    finally:
        p = models.price(model)
        cost = (tok["in_text"] * p["in_text"] + tok["in_audio"] * p["in_audio"] + tok["out_text"] * p["out_text"]
                + (tok["out_audio"] + tok["think"]) * p["out_audio"]) / 1_000_000
        if any(tok.values()):
            db.x("INSERT INTO usage_extra(ts,kind,model,in_text,in_audio,out_text,out_audio,think,cost_usd) VALUES(?,?,?,?,?,?,?,?,?)",
                 (time.time(), "Ansage/Stimmprobe", model, tok["in_text"], tok["in_audio"], tok["out_text"], tok["out_audio"],
                  tok["think"], cost))
    if compact:
        pcm = bytearray(compact_pcm(bytes(pcm)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(pcm))
    data = buf.getvalue()
    # atomar schreiben: parallele Anfragen oder ein Abbruch hinterlassen sonst eine halbe Datei, die danach als Cache gilt
    tmp = f"{path}.{os.getpid()}.{id(buf)}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return data


async def _tts(model, cfg, text, pcm, count):
    async with gclient.client(genai, "live").aio.live.connect(model=model, config=cfg) as s:
        await s.send_realtime_input(text=text)

        async def collect():
            async for r in s.receive():
                if r.usage_metadata:
                    count(r.usage_metadata)
                sc = r.server_content
                if sc and sc.model_turn:
                    for p in sc.model_turn.parts:
                        if p.inline_data and p.inline_data.data:
                            pcm.extend(p.inline_data.data)
                if sc and sc.turn_complete:
                    return
        await asyncio.wait_for(collect(), 30)

