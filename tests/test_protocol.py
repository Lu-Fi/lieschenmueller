"""protocol.py: Builder und Parser des Antwortprotokolls Bridge -> Dialplan (Rundlauf, Validierung, bisherige Zeichenketten)."""
import os
import re

import pytest

from app import protocol as P

CASES = [
    (P.Answer(), "ok"),
    (P.NoAgent(), "no-agent"),
    (P.Hangup(), "hangup"),
    (P.Wait(15), "wait:15"),
    (P.Wait(60, "r_ab12cd34", P.RingPrep(2, "", 1)), "wait:60:r_ab12cd34:2::1"),
    (P.Wait(30, "default"), "wait:30:default"),
    (P.Dial("**610"), "dial:**610"),
    (P.Dial("**610+**620", "default"), "dial:**610+**620:default"),
    (P.Rec("**610"), "rec:**610"),
    (P.FDial("**610"), "fdial::**610"),
    (P.FDial("**610", 20), "fdial:20:**610"),
    (P.FDial("**610+0171555", 30, "r_ab12cd34", P.RingPrep(3, "plus", 2)), "fdial:30:**610+0171555:r_ab12cd34:3:plus:2"),
    (P.FDial("99613", 20, "", P.RingPrep(3, "plus")), "fdial:20:99613::3:plus:"),
    (P.RecX("**610", fallback=True, ring=20), "recx:f:**610:20"),
    (P.RecX("**610", quiet=True, fallback=True, ring=20, moh="default"), "recx:qf:**610:20:default"),
    (P.RecX("**610", fallback=True, ring=20, moh="default", prep=P.RingPrep(3, "plus")), "recx:f:**610:20:default:3:plus:"),
    (P.RecX("**610", fallback=True, prep=P.RingPrep(0, "zz")), "recx:f:**610::::zz:"),
    (P.RecX("**610"), "recx::**610"),
    (P.RecX("0301234567", outgoing=True), "recx:o:0301234567"),
    (P.RecX("0301234567", quiet=True, outgoing=True), "recx:oq:0301234567"),
    (P.SDial("**610", 25, "default"), "sdial:**610:25:default:"),
    (P.SDial("**610", 25, "", record=True, quiet=True), "sdial:**610:25::rq"),
    (P.SDial("**610+**611", 10, "m_melodie", record=True), "sdial:**610+**611:10:m_melodie:r"),
]


@pytest.mark.parametrize("obj,text", CASES, ids=[t for _, t in CASES])
def test_encode_and_roundtrip(obj, text):
    assert obj.encode() == text
    assert P.parse(text) == obj
    assert P.parse(text).encode() == text


def test_recx_music_without_ring_is_positional():
    """Bisher entstand hier 'recx:f:N:<musik>' (Musik landete im Feld Klingelzeit). Jetzt steht die Musik an Position 5."""
    assert P.RecX("**610", fallback=True, moh="default").encode() == "recx:f:**610::default"


@pytest.mark.parametrize("bad", ["", "dial:", "dial:abc", "dial:**610:x y", "fdial:20", "fdial:abc:**610", "recx:z:**610",
                                 "sdial:**610:25", "sdial:**610:25::x", "wait:", "wait:0", "foo", "fdial:20:**610::99:plus:",
                                 "fdial:20:**610::1:xx:", "dial:12", "dial:**12345"])
def test_parse_rejects(bad):
    with pytest.raises(P.ProtocolError):
        P.parse(bad)


def test_builders_validate():
    with pytest.raises(P.ProtocolError):
        P.Dial("0171 555")
    with pytest.raises(P.ProtocolError):
        P.Dial("**610;Hangup")
    with pytest.raises(P.ProtocolError):
        P.FDial("**610", moh="a:b")
    with pytest.raises(P.ProtocolError):
        P.SDial("**610", 25, quiet=True)
    with pytest.raises(P.ProtocolError):
        P.RingPrep(11)


def test_helpers():
    assert P.targets_of("dial:**610+**620:default") == "**610+**620"
    assert P.targets_of("rec:**610") == "**610"
    assert P.targets_of("sdial:**610:25:default:") == "**610"
    assert P.targets_of("hangup") == "" and P.targets_of("kaputt") == ""
    assert P.first_target("fdial:20:**610+**620") == "**610"
    assert P.is_screen("sdial:**610:25::") and not P.is_screen(None) and not P.is_screen("dial:**610")
    assert P.screen_result("accept") == "accept" and P.screen_result(None) == "none" and P.screen_result("x") == "none"


def test_no_protocol_strings_outside_module():
    """main.py/call.py setzen keine Protokoll-Zeichenketten mehr selbst zusammen."""
    root = os.path.join(os.path.dirname(__file__), "..", "bridge", "app")
    pat = re.compile(r"""["'](dial|fdial|recx|rec|sdial|wait):|return\s+["'](no-agent|hangup)["']""")
    for name in ("main.py", "call.py"):
        src = open(os.path.join(root, name), encoding="utf-8").read()
        hits = [l.strip() for l in src.splitlines() if pat.search(l) and not l.strip().startswith("#")]
        assert not hits, (name, hits)
