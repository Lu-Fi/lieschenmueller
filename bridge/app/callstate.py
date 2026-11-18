"""Zustand je Anruf: EIN Objekt (CallRecord) je Asterisk-Anruf-UUID in einer Registry, statt vieler globaler Woerterbuecher.

Ein Anruf (CALLUUID aus dem Dialplan) durchlaeuft diese Phasen:

| Phase | Bedeutung | typischer Eintritt |
|---|---|---|
| klingelt | Telefone klingeln (fdial/recx/wait), noch keine KI | /internal/call -> route_call oder wait:N |
| ki | KI-Sitzung angemeldet oder laufend | /internal/call -> ok, /internal/fallback, /internal/busy |
| rueckfrage | KI fragt bei der angerufenen Person nach (sdial, [screen]) | KI-Sitzung endet mit sdial |
| verbunden | Gespraech zwischen Menschen | /internal/answered, /internal/rec-start, Durchstellen |
| beendet | Asterisk hat aufgelegt (h-Erweiterung, /internal/rec-done) | /internal/rec-done |

Uebergaenge sind informativ (Anzeige, Fehlersuche): ein unerwarteter Wechsel wird protokolliert, aber nie verhindert, damit ein
Anruf im Betrieb nicht an der Buchfuehrung scheitert. Die Rueckfrage-Sitzung (zweite KI, eigene UUID) ist ein eigener Eintrag mit
kind='screen'.

Aufraeumen an EINER Stelle: Registry.expire() (vom Janitor alle 15 s aufgerufen) mit den Ablaufzeiten in TTL; ein Eintrag ohne Inhalt
verschwindet. finish() (beim Auflegen) leert alles ausser der laufenden KI-Sitzung, so kann kein Rest (z. B. ein altes 'after')
spaeter wieder greifen.
"""
import enum
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("callstate")


class Phase(str, enum.Enum):
    RINGING = "klingelt"
    AI = "ki"
    SCREENING = "rueckfrage"
    CONNECTED = "verbunden"
    ENDED = "beendet"


# erlaubte Uebergaenge (alles andere wird nur protokolliert)
ALLOWED = {
    None: {Phase.RINGING, Phase.AI, Phase.CONNECTED, Phase.ENDED},
    Phase.RINGING: {Phase.AI, Phase.CONNECTED, Phase.ENDED, Phase.RINGING},
    Phase.AI: {Phase.AI, Phase.SCREENING, Phase.CONNECTED, Phase.RINGING, Phase.ENDED},
    Phase.SCREENING: {Phase.AI, Phase.CONNECTED, Phase.ENDED},
    Phase.CONNECTED: {Phase.AI, Phase.CONNECTED, Phase.ENDED},
    Phase.ENDED: {Phase.ENDED},
}

# Ablaufzeiten in Sekunden je Feld (gemessen ab dem Setzen des Feldes); None = laeuft nicht ab (laufende KI-Sitzung)
TTL = {
    "after": 120,          # Antwort fuer /internal/after: der Dialplan fragt sofort nach dem Gespraech
    "fallback": 300,       # "niemand nimmt ab -> KI": laenger als jede Klingelzeit
    "retarget": 300,       # Ziel fuer /internal/busy (Durchstellen nach der KI)
    "pending": 90,         # KI angemeldet, AudioSocket kam nie (Janitor markiert den Anruf als abgebrochen)
    "screen": 900,         # Rueckfrage-Kontext des wartenden Anrufers
    "chan": 7200,          # Asterisk-Kanal (Umleiten, Aufnahme stoppen, Auflegen)
    "ringing": 7200,       # Anzeige "klingelt" (die Anzeige selbst blendet nach 90 s aus)
    "human": 7200,         # laufendes Gespraech zwischen Menschen (Anzeige)
    "screen_tried": 7200,  # Ziele, bei denen die Rueckfrage nicht zum Verbinden fuehrte
    "defer_post": 7200,    # Auswertung/Mail erst nach dem ganzen Gespraech
}
FIELDS = tuple(TTL) + ("active",)


@dataclass
class CallRecord:
    uuid: str
    kind: str = "call"                 # 'call' oder 'screen' (Rueckfrage-Sitzung)
    created: float = field(default_factory=time.time)
    phase: Phase = None
    history: list = field(default_factory=list)      # [(Zeit, Phase)]
    chan: dict = None                  # {"chan", "to", "t"}: Asterisk-Kanal und gewaehlte Nummer
    ringing: dict = None               # {"id", "caller", "called", "t", "info"}: Anzeige "klingelt"
    pending: dict = None               # Anmeldung fuer die naechste KI-Sitzung (meta fuer Call)
    fallback: dict = None              # Kontext "niemand nimmt ab -> KI" (/internal/fallback)
    retarget: dict = None              # {"t", "target"}: Durchstellen nach der KI, fuer /internal/busy
    after: tuple = None                # (Antwort fuer /internal/after, Zeitpunkt)
    active: object = None              # laufende KI-Sitzung (call.Call)
    defer_post: bool = None            # Auswertung/Mail erst beim Auflegen (Rueckfrage lief)
    screen: dict = None                # Rueckfrage-Kontext (who, reason, call_id, agent, screen={result}, final)
    screen_tried: set = None           # Ziffern der Ziele, bei denen die Rueckfrage nicht verbunden hat
    human: dict = None                 # Gespraech zwischen Menschen (id, chan, caller, called, rec, since ...)
    stamps: dict = field(default_factory=dict)        # Feld -> Zeitpunkt des Setzens (fuer die Ablaufzeiten)

    def set(self, name, value):
        setattr(self, name, value)
        if value is None:
            self.stamps.pop(name, None)
        else:
            self.stamps[name] = time.time()

    def take(self, name, default=None):
        """Feld lesen und leeren (wie dict.pop)."""
        v = getattr(self, name)
        self.set(name, None)
        return default if v is None else v

    def empty(self):
        return all(getattr(self, f) is None for f in FIELDS)

    def to(self, phase):
        """Phasenwechsel (informativ): unerwartete Wechsel werden protokolliert, nie verhindert."""
        if phase == self.phase:
            return
        if phase not in ALLOWED.get(self.phase, ()):
            log.info("Anruf %s: unerwarteter Phasenwechsel %s -> %s", self.uuid[:8], self.phase and self.phase.value, phase.value)
        self.phase = phase
        self.history.append((time.time(), phase))


class Registry:
    def __init__(self):
        self._r = {}

    def __len__(self):
        return len(self._r)

    def get(self, uuid):
        return self._r.get(uuid)

    def ensure(self, uuid, kind=None):
        r = self._r.get(uuid)
        if r is None:
            r = self._r[uuid] = CallRecord(uuid)
        if kind:
            r.kind = kind
        return r

    def records(self):
        return list(self._r.values())

    def clear(self):
        self._r.clear()

    def _gc(self, uuid):
        r = self._r.get(uuid)
        if r is not None and r.empty():
            del self._r[uuid]

    # --- Felder lesen/schreiben (fuer main.py) ---
    def field(self, uuid, name, default=None):
        r = self._r.get(uuid)
        v = getattr(r, name) if r else None
        return default if v is None else v

    def put(self, uuid, name, value):
        self.ensure(uuid).set(name, value)
        if value is None:
            self._gc(uuid)

    def take(self, uuid, name, default=None):
        r = self._r.get(uuid)
        if r is None:
            return default
        v = r.take(name, default)
        self._gc(uuid)
        return v

    def having(self, name):
        """[(uuid, Wert)] aller Eintraege, bei denen das Feld gesetzt ist."""
        return [(u, getattr(r, name)) for u, r in list(self._r.items()) if getattr(r, name) is not None]

    def phase(self, uuid, phase):
        self.ensure(uuid).to(phase)

    def ended(self, uuid):
        """Hat Asterisk diesen Anruf schon aufgelegt (finish)?"""
        r = self._r.get(uuid)
        return bool(r and r.phase == Phase.ENDED)

    def finish(self, uuid):
        """Asterisk hat aufgelegt (/internal/rec-done): alles ausser der laufenden KI-Sitzung leeren, Phase 'beendet'."""
        r = self._r.get(uuid)
        if r is None:
            return
        for f in FIELDS:
            if f != "active":
                r.set(f, None)
        r.to(Phase.ENDED)
        self._gc(uuid)

    def expire(self, now=None):
        """Abgelaufene Felder leeren (EINZIGE Stelle fuer Ablaufzeiten). Rueckgabe: [(uuid, Feld, Wert)] der entfernten Werte,
        damit der Aufrufer Folgen ausloesen kann (z. B. abgebrochene Anmeldung in der Anrufliste markieren)."""
        now = now or time.time()
        gone = []
        for uuid, r in list(self._r.items()):
            for name, ttl in TTL.items():
                v = getattr(r, name)
                if v is None or ttl is None:
                    continue
                t = self._stamp(r, name, v)
                if now - t > ttl:
                    r.set(name, None)
                    gone.append((uuid, name, v))
            self._gc(uuid)
        return gone

    @staticmethod
    def _stamp(r, name, v):
        """Zeitbasis wie bisher: eigene Zeitangabe des Eintrags, sonst der Zeitpunkt des Setzens."""
        if name == "after":
            return v[1]
        if name == "pending":
            return v.get("created", r.stamps.get(name, 0))
        if name == "screen":
            return v.get("t", r.stamps.get(name, 0))
        if isinstance(v, dict) and "t" in v:
            return v["t"]
        return r.stamps.get(name, 0)


CALLS = Registry()
