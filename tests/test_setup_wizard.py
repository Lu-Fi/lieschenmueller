"""Einrichtungsassistent: GET/PUT /api/setup (nur Admins), Stand der Schritte ohne Zugangsdaten in der Antwort, "Später" (meta
setup_dismissed), einmal vollstaendig (meta setup_done) -> erscheint nicht mehr von selbst. Ausserdem: Oberflaeche enthaelt den Assistenten."""
import os

import pytest

from app import config, fritz, main
from tests.conftest import ROOT, run
from tests.test_settings import HOST, admin

SECRET_KEYS = ("GEMINI_API_KEY", "FRITZ_USER", "FRITZ_PASSWORD", "FRITZ_HOST", "FRITZ_TR064_USER", "FRITZ_TR064_PASSWORD")


@pytest.fixture
def clean_env(monkeypatch):
    for k in SECRET_KEYS:                       # nach dem Test wieder ohne diese Variablen (config.apply setzt sie in os.environ)
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)


def test_setup_requires_admin(bridge, clean_env):
    async def scenario(b):
        r = await b.http.get("/api/setup")
        assert r.status_code in (401, 403)
        r = await b.http.put("/api/setup", json={"dismissed": True})
        assert r.status_code in (401, 403)
    run(bridge, scenario)


def test_setup_steps_progress_and_no_secrets(bridge, clean_env):
    @admin
    async def scenario(b):
        r = (await b.http.get("/api/setup")).json()
        assert r["steps"] == {"gemini": False, "fritz": False, "sip": False, "registered": False}
        assert r["show"] and not r["complete"] and r["fritz_host"] == "192.168.178.1" and r["sip_pw_ok"] is None
        assert r["rec_prefix"] == "*77"
        config.set_value("gemini_api_key", "AIzaTest-geheim-123")
        config.set_value("fritz_host", HOST)
        config.set_value("fritz_tr064_user", "lieschen-api")
        config.set_value("fritz_tr064_password", "tr064-Geheim")
        b.ami.reg_status = "Rejected"
        r = (await b.http.get("/api/setup")).json()
        assert r["steps"] == {"gemini": True, "fritz": True, "sip": False, "registered": False} and r["show"]
        config.set_value("fritz_user", "LieschenMueller")
        config.set_value("fritz_password", "mit#Raute!")
        r = await b.http.get("/api/setup")
        j = r.json()
        assert j["steps"]["sip"] and not j["steps"]["registered"] and j["reg"] == "Rejected" and j["sip_pw_ok"] is False
        for secret in ("AIzaTest-geheim-123", "tr064-Geheim", "mit#Raute!"):
            assert secret not in r.text
        config.set_value("fritz_password", "Gutes-Kennwort_1.0")
        b.ami.reg_status = "Registered"
        j = (await b.http.get("/api/setup")).json()
        assert j["complete"] and not j["show"] and j["sip_pw_ok"] is True and b.db.get_meta("setup_done") == "1"
        b.ami.reg_status = "Unregistered"                        # spaetere Stoerung: Assistent erscheint nicht wieder von selbst
        j = (await b.http.get("/api/setup")).json()
        assert not j["complete"] and j["done_once"] and not j["show"]
    run(bridge, scenario)


def test_setup_dismiss_and_reopen(bridge, clean_env):
    @admin
    async def scenario(b):
        r = await b.http.put("/api/setup", json={"dismissed": True})
        assert r.status_code == 200 and r.json() == {"dismissed": True}
        j = (await b.http.get("/api/setup")).json()
        assert j["dismissed"] and not j["show"] and not j["complete"]
        await b.http.put("/api/setup", json={"dismissed": False})
        assert (await b.http.get("/api/setup")).json()["show"]
    run(bridge, scenario)


def test_setup_check_uses_write_block(bridge, clean_env, monkeypatch):
    @admin
    async def scenario(b):
        for k, v in (("fritz_host", HOST), ("fritz_tr064_user", "u"), ("fritz_tr064_password", "p"), ("fritz_user", "LieschenMueller"),
                     ("fritz_password", "mit#Raute!")):
            config.set_value(k, v)
        monkeypatch.setattr(fritz, "_conn", lambda: object())
        monkeypatch.setattr(fritz, "write_block", lambda fc, phone="": f"Kennwort von {phone} passt nicht")
        j = (await b.http.get("/api/setup?check=1")).json()
        assert j["pw_block"] == "Kennwort von LieschenMueller passt nicht"

        def down():
            raise ConnectionError("weg")
        monkeypatch.setattr(fritz, "_conn", down)
        j = (await b.http.get("/api/setup?check=1")).json()
        assert "pw_block" not in j and "nicht erreichbar" in j["pw_block_error"]
    run(bridge, scenario)


def test_ui_has_wizard_using_existing_endpoints():
    html = open(os.path.join(ROOT, "bridge", "app", "static", "index.html"), encoding="utf-8").read()
    for needle in ('"/api/setup"', "function setupView", "/api/config/test-gemini", "/api/config/test-fritz", "astNums()",
                   "aistudio.google.com", "Zugriff für Anwendungen zulassen", "setupLater", '"setup"'):
        assert needle in html, needle
