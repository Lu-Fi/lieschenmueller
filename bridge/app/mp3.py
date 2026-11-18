"""Aufnahmen als MP3 ablegen (klein, im Browser und Mailprogramm abspielbar) und fuer die Fernabfrage wieder lesen.
Nutzt lameenc (Encoder) und miniaudio (Decoder), beide ohne Systempakete."""
import logging
import os
import wave

log = logging.getLogger("mp3")
BITRATES = (16, 24, 32, 48, 64)       # bei 8 kHz sind nur diese Stufen zulaessig; 32 = guter Standard fuer Sprache


def bitrate():
    from . import db
    try:
        k = int(db.get_meta("mp3_kbps", "32") or 32)
    except ValueError:
        k = 32
    return k if k in BITRATES else 32


def wav_to_mp3(src, dst):
    """WAV (16 Bit PCM) nach MP3 (Sprache, 8 kHz). Bei Fehler wirft die Funktion; die Quelle bleibt dann unveraendert."""
    import lameenc
    with wave.open(src) as w:
        ch, rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
        if width != 2 or ch not in (1, 2):
            raise ValueError(f"WAV-Format nicht unterstuetzt ({ch} Kanaele, {width * 8} Bit)")
        enc = lameenc.Encoder()
        enc.set_bit_rate(bitrate())
        enc.set_in_sample_rate(rate)
        enc.set_out_sample_rate(rate)
        enc.set_channels(ch)
        enc.set_quality(2)
        tmp = dst + ".part"
        with open(tmp, "wb") as out:
            while True:
                data = w.readframes(8000 * 10)
                if not data:
                    break
                out.write(enc.encode(data))
            out.write(enc.flush())
    os.replace(tmp, dst)
    return dst


def to_mp3_inplace(wav_path):
    """Datei neben der WAV als .mp3 anlegen und die WAV loeschen; gibt den neuen Pfad zurueck (bei Fehler den alten)."""
    dst = os.path.splitext(wav_path)[0] + ".mp3"
    try:
        wav_to_mp3(wav_path, dst)
        os.remove(wav_path)
        return dst
    except Exception:
        log.exception("MP3-Konvertierung fehlgeschlagen - WAV bleibt erhalten")
        if os.path.exists(dst + ".part"):
            os.remove(dst + ".part")
        return wav_path


def wait_wav(path, tries=10, delay=1.0):
    """Wartet, bis eine WAV lesbar ist (Asterisk schliesst sie nach dem Auflegen eventuell erst kurz danach); True wenn lesbar."""
    import time
    for i in range(tries):
        try:
            with wave.open(path) as w:
                if w.getnframes() > 0:
                    return True
        except (wave.Error, EOFError, OSError):
            pass
        time.sleep(delay)
    return False


def append_audio(first, second_wav, out_wav, gap_s=1.0):
    """Erste Aufnahme (mp3 oder wav, Stereo oder Mono) + Pause + zweite WAV (Mono oder Stereo, 8 kHz) zu EINER Mono-WAV zusammenfuegen
    (blockierend). Stereo-Teile (Anrufer links, KI rechts) werden zu einer Spur gemischt, damit beide Seiten ueberall hoerbar sind."""
    import audioop
    import miniaudio
    n1 = miniaudio.get_file_info(first).nchannels
    dec = miniaudio.decode_file(first, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=n1, sample_rate=8000)
    head = _mono(dec.samples.tobytes(), n1)
    with wave.open(second_wav) as w:
        ch, rate = w.getnchannels(), w.getframerate()
        if rate != 8000 or w.getsampwidth() != 2 or ch not in (1, 2):
            raise ValueError("Teil 2 hat ein unerwartetes Format")
        part = _mono(w.readframes(w.getnframes()), ch)
    tmp = out_wav + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(head)
        w.writeframes(b"\x00" * (int(gap_s * 8000) * 2))
        w.writeframes(part)
    os.replace(tmp, out_wav)


def _mono(pcm, channels):
    """16-Bit-PCM mit 1 oder 2 Kanaelen zu Mono: bei Stereo Summe beider Kanaele (die Seiten sprechen selten gleichzeitig)."""
    import audioop
    if channels == 1:
        return pcm
    return audioop.add(audioop.tomono(pcm, 2, 1, 0), audioop.tomono(pcm, 2, 0, 1), 2)


def mp3_to_pcm8k(path):
    """MP3 -> 16 Bit Mono PCM bei 8 kHz (fuer die Wiedergabe im Telefonat)."""
    import miniaudio
    d = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=8000)
    return d.samples.tobytes()
