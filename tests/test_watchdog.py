"""ops/watchdog.py: prueft die tatsaechlich eingetragenen Klingel-Rufnummern 99NNN (data/asterisk/fritz.conf, seit 2026-11) statt
fest 99613 in asterisk/conf/pjsip.conf; ohne Klingel-Rufnummer nur die Grundleitung (Anmeldung bei der Fritzbox)."""
import importlib.util
import os

import pytest

from app import fritzconf
from tests.conftest import ROOT


@pytest.fixture
def wd(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("lm_watchdog", os.path.join(ROOT, "ops", "watchdog.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    (tmp_path / "data" / "asterisk").mkdir(parents=True)
    (tmp_path / ".env").write_text("WEB_PORT=1\n")          # Port 1: die Bridge "antwortet nicht" (kein Netz im Test)
    out = {"core show uptime": "System uptime: 1 minute", "pjsip show registrations": "fritz-reg/sip:192.168.178.1  fritz-auth  Registered",
           "pjsip show contacts": ""}
    monkeypatch.setattr(mod, "ast", lambda cmd: (0, out[cmd]))
    mod.OUT = out
    return mod


def write_fritz(tmp_path, ring, ring_ok=None):
    text = fritzconf.render("LieschenMueller", "Kennwort-1", "192.168.178.1", acl=False, ring=ring, ring_ok=ring_ok)
    (tmp_path / "data" / "asterisk" / "fritz.conf").write_text(text)


def test_ring_numbers_from_fritz_conf(wd, tmp_path):
    write_fritz(tmp_path, ["99613", "99610"], ring_ok=["99613"])
    assert wd.ring_numbers() == ["99613", "99610"]            # alle eingetragenen (AOR), auch ohne Endpoint
    wd.OUT["pjsip show contacts"] = "Contact:  99613/sip:99613@192.168.178.1:5060  abc  Avail  12.3"
    bad = wd.checks()
    assert set(bad) == {"bridge", "ring:99610"} and "99610" in bad["ring:99610"]
    wd.OUT["pjsip show contacts"] += "\nContact:  99610/sip:99610@192.168.178.1:5060  def  Avail  11.0"
    assert set(wd.checks()) == {"bridge"}


def test_no_ring_numbers_only_base_line(wd, tmp_path):
    write_fritz(tmp_path, [])
    assert wd.ring_numbers() == []
    assert set(wd.checks()) == {"bridge"}
    wd.OUT["pjsip show registrations"] = "fritz-reg/sip:192.168.178.1  fritz-auth  Rejected"
    assert set(wd.checks()) == {"bridge", "fritz-reg"}


def test_legacy_pjsip_conf_and_placeholder(wd, tmp_path):
    (tmp_path / "data" / "asterisk" / "fritz.conf").write_text("; Fritzbox noch nicht eingerichtet\n")
    (tmp_path / "asterisk" / "conf").mkdir(parents=True)
    (tmp_path / "asterisk" / "conf" / "pjsip.conf").write_text("[99613]\ntype=aor\nmax_contacts=1\n\n[99613]\ntype=endpoint\n")
    assert wd.ring_numbers() == ["99613"]
    assert "ring:99613" in wd.checks()


def test_ring_alarm_needs_seven_minutes(wd, tmp_path, monkeypatch):
    write_fritz(tmp_path, ["99610"])
    monkeypatch.setattr(wd, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(wd, "heal", lambda: [])
    mails = []
    monkeypatch.setattr(wd, "mail", lambda subject, text: mails.append((subject, text)))
    monkeypatch.setattr(wd, "checks", lambda: {"ring:99610": "weg"})
    for _ in range(6):
        wd.main()
    assert not mails
    wd.main()
    assert mails and "weg" in mails[0][1]
