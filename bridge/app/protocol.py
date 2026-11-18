"""Protokoll zwischen Dialplan (asterisk/templates/extensions.conf.tpl) und Bridge.

Der Dialplan fragt die Bridge per CURL (/internal/*) und bekommt Klartext zurueck: ein Schluesselwort und Felder, mit ':' getrennt.
Er zerlegt die Antwort mit ${RES:n} und ${CUT(RES,:,n)}. Dieses Modul ist die EINZIGE Stelle, die diese Zeichenketten baut und
liest: main.py/call.py bauen Antworten nur ueber die Klassen hier und lesen sie nur ueber parse().

Antworten (Feldnummer = Position fuer CUT(RES,:,n); das Schluesselwort ist Feld 1):

| Antwort | Felder | Bedeutung im Dialplan |
|---|---|---|
| `ok` | – | KI nimmt an: Answer + AudioSocket (Label answer) |
| `wait:<s>[:<moh>[:<verz>:<cid>:<toene>]]` | 2 = Sekunden, 3 = Wartemusik-Klasse, 4-6 = ringprep | erst warten, dann KI: ohne Musik klingeln lassen (Ringing, Wait), mit Musik selbst annehmen und Musik spielen |
| `no-agent` | – | nicht annehmen: Hangup(19) (Fritzbox klingelt weiter) bzw. ausgehend Hangup(21) |
| `hangup` | – | nur /internal/after: nichts mehr tun, auflegen |
| `dial:<ziele>[:<moh>]` | 2 = Ziele, 3 = Wartemusik-Klasse | durchstellen (Label direct); niemand nimmt ab -> /internal/busy |
| `fdial:<ring>:<ziele>[:<moh>[:<verz>:<cid>:<toene>]]` | 2 = Klingelzeit (leer = 25 s), 3 = Ziele, 4 = Musik waehrend des Klingelns, 5-7 = ringprep | Asterisk nimmt an und laesst klingeln; niemand nimmt ab -> /internal/fallback (KI) |
| `recx:<flags>:<ziele>[:<ring>[:<moh>[:<verz>:<cid>:<toene>]]]` | 2 = Flags, 3 = Ziele, 4 = Klingelzeit, 5 = Musik, 6-8 = ringprep | Anruf mit Aufnahme ab Annahme; Flags q = ohne Hinweis-Ansage, f = KI-Rueckfall, o = ausgehend |
| `rec:<ziele>` | 2.. = Ziele (Rest der Zeile) | nach der KI durchstellen mit Mitschrift (Label recdial) |
| `sdial:<ziele>:<ring>:<moh>:<flags>` | 2 = Ziele, 3 = Klingelzeit, 4 = Musik, 5 = Flags (r = aufnehmen, q = ohne Hinweis) | Rueckfrage vor dem Verbinden ([screen]) |
| Rueckfrage | `ok`/`skip` (screen-start), `accept`/`decline`/`voicemail`/`none` (screen-result) | nur `accept` verbindet |

ringprep (Klingeln ueber Klingel-Rufnummern 99NNN): <verz> = Verzoegerung in s (0-10, leer = keine), <cid> = Schreibweise der Anrufernummer
(leer, `plus` = +49…, `zz` = 0049…), <toene> = Zahl der Freizeichen-Toene vor der Musik (leer = keine). Die drei Felder stehen nur
da, wenn mindestens eines gesetzt ist.

Ziele: eine oder mehrere Nummern mit '+' (klingeln gleichzeitig), jede nur Ziffern (3-20) oder intern `**N` (vorher mit
ami.dial_number geprueft). Wartemusik-Klassen: `default`, `m_<id>`, `r_<id>` (hold.py).

JSON: Asterisk 23 im Image kann JSON_DECODE (lokal geprueft, tests/integration). Die Umstellung ist bewusst NICHT gemacht (siehe
docs/ARCHITEKTUR.md, Abschnitt Protokoll); sollte sie kommen, ist dieses Modul die einzige Stelle in der Bridge, die sich aendert.
"""
import re
from dataclasses import dataclass, field

TARGET_RE = re.compile(r"(\*\*\d{1,4}|\d{3,20})")
MOH_RE = re.compile(r"[A-Za-z0-9_]{0,40}")
CIDS = ("", "plus", "zz")
SCREEN_RESULTS = ("accept", "decline", "voicemail", "pass", "none")      # pass = an einen Kollegen weitergeben (zurueck zur KI des Anrufers)
OK, NO_AGENT, HANGUP, SKIP = "ok", "no-agent", "hangup", "skip"


class ProtocolError(ValueError):
    """Antwort passt nicht zum Protokoll (Bauen oder Lesen)."""


def _targets(t):
    """Ziele als Tupel; akzeptiert 'n1+n2', ['n1', 'n2'] oder ('n1',)."""
    parts = t.split("+") if isinstance(t, str) else list(t)
    if not parts or not all(isinstance(p, str) and TARGET_RE.fullmatch(p) for p in parts):
        raise ProtocolError(f"ungueltige Ziele: {t!r}")
    return tuple(parts)


def _moh(m):
    m = m or ""
    if not MOH_RE.fullmatch(m):
        raise ProtocolError(f"ungueltige Wartemusik-Klasse: {m!r}")
    return m


def _int(v, lo, hi, name):
    if v in (None, ""):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ProtocolError(f"{name}: keine Zahl ({v!r})")
    if not lo <= n <= hi:
        raise ProtocolError(f"{name}: {n} nicht in {lo}..{hi}")
    return n


def _s(n):
    return "" if n in (None, 0) else str(n)


@dataclass(frozen=True)
class RingPrep:
    """Vorbereitung des Klingelns ueber Klingel-Rufnummern 99NNN (Dialplan [ringprep]): Verzoegerung, Schreibweise der Anrufernummer, Freizeichen-Toene."""
    delay: int = 0
    cid: str = ""
    tones: int = 0

    def __post_init__(self):
        object.__setattr__(self, "delay", _int(self.delay, 0, 10, "Verzoegerung") or 0)
        object.__setattr__(self, "tones", _int(self.tones, 0, 8, "Toene") or 0)
        if self.cid not in CIDS:
            raise ProtocolError(f"Schreibweise der Anrufernummer: {self.cid!r}")

    def __bool__(self):
        return bool(self.delay or self.cid or self.tones)

    def fields(self):
        return [_s(self.delay), self.cid, _s(self.tones)] if self else []

    @classmethod
    def parse(cls, f):
        f = list(f) + ["", "", ""]
        return cls(_int(f[0], 0, 10, "Verzoegerung") or 0, f[1], _int(f[2], 0, 8, "Toene") or 0)


@dataclass(frozen=True)
class Answer:
    """KI nimmt sofort an."""
    def encode(self):
        return OK


@dataclass(frozen=True)
class NoAgent:
    """Nicht annehmen (kein Agent, Kostenbremse, ungueltiges Ziel)."""
    def encode(self):
        return NO_AGENT


@dataclass(frozen=True)
class Hangup:
    """Nach dem KI-Gespraech: nichts mehr tun."""
    def encode(self):
        return HANGUP


@dataclass(frozen=True)
class Wait:
    """Erst warten, nach `seconds` nimmt die KI an. Ohne moh klingelt die Fritzbox selbst (Ringing); mit moh nimmt Asterisk sofort an und spielt
    Wartemusik (Klasse moh, ggf. mit Freizeichen-Toenen davor, siehe RingPrep), ohne dass ein Telefon klingelt."""
    seconds: int
    moh: str = ""
    prep: RingPrep = field(default_factory=RingPrep)

    def __post_init__(self):
        object.__setattr__(self, "seconds", _int(self.seconds, 1, 600, "Wartezeit"))
        if self.seconds is None:
            raise ProtocolError("Wartezeit fehlt")
        object.__setattr__(self, "moh", _moh(self.moh))

    def encode(self):
        tail = [self.moh] + self.prep.fields() if (self.moh or self.prep) else []
        return ":".join(["wait", str(self.seconds)] + tail)


@dataclass(frozen=True)
class Dial:
    """Durchstellen ohne KI-Rueckfall beim Klingeln (nach /internal/busy nimmt die KI wieder an). moh: Musik statt Freizeichen."""
    targets: tuple
    moh: str = ""

    def __post_init__(self):
        object.__setattr__(self, "targets", _targets(self.targets))
        object.__setattr__(self, "moh", _moh(self.moh))

    def encode(self):
        return "dial:" + "+".join(self.targets) + (":" + self.moh if self.moh else "")


@dataclass(frozen=True)
class FDial:
    """Klingeln mit KI-Rueckfall: ring = Klingelzeit (None = Dialplan-Standard 25 s), moh = Musik waehrend des Klingelns."""
    targets: tuple
    ring: int = None
    moh: str = ""
    prep: RingPrep = field(default_factory=RingPrep)

    def __post_init__(self):
        object.__setattr__(self, "targets", _targets(self.targets))
        object.__setattr__(self, "ring", _int(self.ring, 1, 600, "Klingelzeit"))
        object.__setattr__(self, "moh", _moh(self.moh))

    def encode(self):
        tail = [self.moh] + self.prep.fields() if (self.moh or self.prep) else []
        return ":".join(["fdial", _s(self.ring), "+".join(self.targets)] + tail)


@dataclass(frozen=True)
class RecX:
    """Anruf mit Aufnahme, von Asterisk gefuehrt. quiet = ohne Hinweis-Ansage, fallback = KI uebernimmt, wenn niemand abnimmt,
    outgoing = ausgehendes Gespraech eines Telefons (Spuren nicht vertauscht, kein Hinweis an den Mitarbeiter)."""
    targets: tuple
    quiet: bool = False
    fallback: bool = False
    outgoing: bool = False
    ring: int = None
    moh: str = ""
    prep: RingPrep = field(default_factory=RingPrep)

    def __post_init__(self):
        object.__setattr__(self, "targets", _targets(self.targets))
        object.__setattr__(self, "ring", _int(self.ring, 1, 600, "Klingelzeit"))
        object.__setattr__(self, "moh", _moh(self.moh))

    @property
    def flags(self):
        return ("o" if self.outgoing else "") + ("q" if self.quiet else "") + ("f" if self.fallback else "")

    def encode(self):
        parts = ["recx", self.flags, "+".join(self.targets)]
        if self.ring or self.moh or self.prep:
            parts.append(_s(self.ring))
        if self.moh or self.prep:
            parts += [self.moh] + self.prep.fields()
        return ":".join(parts)


@dataclass(frozen=True)
class Rec:
    """Nach dem KI-Gespraech durchstellen mit Mitschrift (beide Seiten getrennt aufgenommen)."""
    targets: tuple

    def __post_init__(self):
        object.__setattr__(self, "targets", _targets(self.targets))

    def encode(self):
        return "rec:" + "+".join(self.targets)


@dataclass(frozen=True)
class SDial:
    """Durchstellen mit Rueckfrage: ring = Klingelzeit, moh = Musik fuer den Anrufer (leer = Freizeichen),
    record/quiet = das verbundene Gespraech aufnehmen (ohne Hinweis-Ansage)."""
    targets: tuple
    ring: int
    moh: str = ""
    record: bool = False
    quiet: bool = False

    def __post_init__(self):
        object.__setattr__(self, "targets", _targets(self.targets))
        object.__setattr__(self, "ring", _int(self.ring, 1, 600, "Klingelzeit"))
        object.__setattr__(self, "moh", _moh(self.moh))
        if self.quiet and not self.record:
            raise ProtocolError("quiet nur zusammen mit record")

    @property
    def flags(self):
        return ("r" if self.record else "") + ("q" if self.quiet else "")

    def encode(self):
        return f"sdial:{'+'.join(self.targets)}:{self.ring}:{self.moh}:{self.flags}"


TRANSFERS = (Dial, Rec, SDial)        # moegliche Antworten von /internal/after (ausser Hangup)


def parse(text):
    """Antwort-Zeichenkette -> Objekt (ProtocolError bei Unbekanntem)."""
    s = (text or "").strip()
    if s == OK:
        return Answer()
    if s == NO_AGENT:
        return NoAgent()
    if s == HANGUP:
        return Hangup()
    kind, _, rest = s.partition(":")
    f = rest.split(":")
    if kind == "wait":
        return Wait(f[0], f[1] if len(f) > 1 else "", RingPrep.parse(f[2:5]))
    if kind == "dial":
        return Dial(f[0], f[1] if len(f) > 1 else "")
    if kind == "rec":
        return Rec(rest)
    if kind == "fdial":
        if len(f) < 2:
            raise ProtocolError(f"fdial ohne Ziele: {s!r}")
        return FDial(f[1], f[0], f[2] if len(f) > 2 else "", RingPrep.parse(f[3:6]))
    if kind == "recx":
        if len(f) < 2 or set(f[0]) - set("oqf"):
            raise ProtocolError(f"recx ungueltig: {s!r}")
        return RecX(f[1], "q" in f[0], "f" in f[0], "o" in f[0], f[2] if len(f) > 2 else None, f[3] if len(f) > 3 else "",
                    RingPrep.parse(f[4:7]))
    if kind == "sdial":
        if len(f) != 4 or set(f[3]) - set("rq"):
            raise ProtocolError(f"sdial ungueltig: {s!r}")
        return SDial(f[0], f[1], f[2], "r" in f[3], "q" in f[3])
    raise ProtocolError(f"unbekannte Antwort: {s!r}")


def first_target(text):
    """Erstes Ziel einer Weiterleitungs-Antwort (dial/rec/sdial/fdial/recx), sonst ''."""
    try:
        r = parse(text)
    except ProtocolError:
        return ""
    return r.targets[0] if hasattr(r, "targets") else ""


def targets_of(text):
    """Alle Ziele einer Weiterleitungs-Antwort als 'n1+n2' (wie im Protokoll), sonst ''."""
    try:
        r = parse(text)
    except ProtocolError:
        return ""
    return "+".join(r.targets) if hasattr(r, "targets") else ""


def is_screen(text):
    """Durchstellen mit Rueckfrage?"""
    return (text or "").startswith("sdial:")


def screen_result(res):
    """Ergebnis der Rueckfrage fuer /internal/screen-result (nur accept verbindet)."""
    return res if res in SCREEN_RESULTS else "none"
