"""Abtastratenwandlung mit Tiefpassfilter (verhindert Aliasing/Knistern in den Hoehen).

AudioSocket: slin 8 kHz. Gemini Live: Eingang 16 kHz, Ausgang 24 kHz.
audioop.ratecv filtert nicht und spiegelt Frequenzen oberhalb der Zielbandbreite nach unten.
"""
import audioop

import numpy as np


def _lowpass(taps, cutoff, fs):
    """Gefensterter Sinc-Tiefpass (Hamming), Summe 1."""
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff / fs * n) * np.hamming(taps)
    return (h / h.sum()).astype(np.float32)


def _to_f32(pcm):
    return np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype="<i2").astype(np.float32)


def _to_pcm(y):
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()


class Down3:
    """24 kHz -> 8 kHz: Bandpass 200 Hz-3,4 kHz (Telefonband, kein Bass-Wummern im Hoerer), optionale Verstaerkung im Filter
    (float, kein int16-Clipping vor dem Begrenzer), dann jede dritte Probe (zustandsbehaftet fuer Datenstroeme)."""

    def __init__(self, gain=1.0):
        self.h = ((_lowpass(127, 3400, 24000) - _lowpass(127, 200, 24000)) * gain).astype(np.float32)
        self.tail = np.zeros(len(self.h) - 1, np.float32)
        self.off = 0

    def f32(self, pcm):
        b = np.concatenate((self.tail, _to_f32(pcm)))
        self.tail = b[-(len(self.h) - 1):]
        y = np.convolve(b, self.h, mode="valid")          # Laenge = Anzahl neuer Proben
        out = y[self.off::3]
        self.off = self.off + 3 * len(out) - len(y)       # Phase fuer den naechsten Block
        return out

    def __call__(self, pcm):
        return _to_pcm(self.f32(pcm))


class Up2:
    """8 kHz -> 16 kHz: Nullen einfuegen, Tiefpass bei 3,6 kHz (entfernt Spiegelfrequenzen)."""

    def __init__(self):
        self.h = _lowpass(63, 3600, 16000) * 2
        self.tail = np.zeros(len(self.h) - 1, np.float32)

    def __call__(self, pcm):
        x = _to_f32(pcm)
        z = np.zeros(len(x) * 2, np.float32)
        z[::2] = x
        b = np.concatenate((self.tail, z))
        self.tail = b[-(len(self.h) - 1):]
        return _to_pcm(np.convolve(b, self.h, mode="valid"))


class _Ratecv:
    def __init__(self, src, dst):
        self.src, self.dst, self.state = src, dst, None

    def __call__(self, pcm):
        out, self.state = audioop.ratecv(pcm, 2, 1, self.src, self.dst, self.state)
        return out


def ai_volume():
    """Lautstaerke der KI-Stimme in Prozent (30 bis 100, Standard 60; Gemini liefert ~-16 dBFS, Ziel ~-22 dBFS)."""
    from . import db
    try:
        return max(30, min(int(db.get_meta("ai_volume", "60") or 60), 100))
    except ValueError:
        return 60


class Leveler:
    """Pegel der KI-Stimme: feste Verstaerkung und weicher Begrenzer (tanh ab ~70 % Vollaussteuerung), damit laute Spitzen nach der Wandlung
    auf 8 kHz / A-law nicht uebersteuern."""

    KNEE = 16000.0

    def __init__(self, percent):
        self.g = percent / 100.0

    def __call__(self, pcm):
        y = (pcm if isinstance(pcm, np.ndarray) else _to_f32(pcm)) * self.g
        big = np.abs(y) > self.KNEE
        if big.any():
            over = np.abs(y[big]) - self.KNEE
            y[big] = np.sign(y[big]) * (self.KNEE + (32767 - self.KNEE) * np.tanh(over / (32767 - self.KNEE)))
        return _to_pcm(y)


def Resampler(src, dst):
    if (src, dst) == (24000, 8000):
        return Down3()
    if (src, dst) == (8000, 16000):
        return Up2()
    return _Ratecv(src, dst)
