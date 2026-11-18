"""Eine Wahrheit fuer Einstellungen: Datenbank -> data/asterisk/fritz.conf (astconf.py), Migrationen adopt/upgrade,
render.py ueberschreibt nie mit leeren Zugangsdaten. Datei und Datenbank laufen absichtlich auseinander; nach Neustart/Render gilt
deterministisch die Datenbank."""
import asyncio
import os
import subprocess
import sys

import pytest

from app import astconf, config, fritzconf, main
from tests.conftest import ROOT, run

HOST = "192.168.178.1"


def file_creds(path=None):
    return fritzconf.parse(open(path or astconf.SIP_FILE, encoding="utf-8").read())


def set_db(user, pw, host=HOST):
    config.set_value("fritz_user", user)
    config.set_value("fritz_password", pw)
    config.set_value("fritz_host", host)


def write_file(user, pw, host=HOST, old_format=False):
    fritzconf.write(astconf.SIP_FILE, user, pw, host)
    if old_format:                          # Datei aus der Zeit vor der IP-Sperre
        t = open(astconf.SIP_FILE).read()
        t = t[:t.index("\n; Nur die Fritzbox")] + fritzconf.REG if "; Nur die Fritzbox" in t else t
        t = t.replace("identify_by=ip\n", "")
        open(astconf.SIP_FILE, "w").write(t)


def test_db_wins_over_diverged_file(bridge):
    set_db("db-user", "db-pass")
    write_file("file-user", "file-pass")
    assert astconf.sync_fritz() == "written"
    assert file_creds() == ("db-user", "db-pass", HOST)
    assert fritzconf.parse(open(astconf.SIP_FILE + ".vorher").read()) == ("file-user", "file-pass", HOST)
    assert astconf.sync_fritz() == "same"                          # deterministisch: zweiter Lauf aendert nichts
    assert config.get("fritz_user") == "db-user"


def test_adopt_only_when_db_empty(bridge):
    write_file("file-user", "file-pass")
    assert astconf.sync_fritz() == "adopted+written"              # Migration Datei -> DB (99613 jetzt in fritz.conf)
    assert (config.get("fritz_user"), config.get("fritz_password"), config.get("fritz_host")) == ("file-user", "file-pass", HOST)
    write_file("other", "other-pass")
    assert astconf.sync_fritz() == "written"                       # jetzt gewinnt die DB
    assert file_creds() == ("file-user", "file-pass", HOST)


def test_upgrade_when_db_incomplete(bridge):
    config.set_value("fritz_user", "nur-benutzer")                 # unvollstaendig: kein Kennwort
    write_file("file-user", "file-pass", old_format=True)
    assert "[fritz-acl]" not in open(astconf.SIP_FILE).read()
    assert astconf.sync_fritz() == "upgraded"                      # Migration: Datei bleibt Quelle, aktuelles Format
    text = open(astconf.SIP_FILE).read()
    assert "[fritz-acl]" in text and "identify_by=ip" in text
    assert file_creds() == ("file-user", "file-pass", HOST)
    assert config.get("fritz_password") == ""


def test_foreign_or_missing_file_untouched(bridge):
    assert astconf.sync_fritz() == "none"
    assert not os.path.exists(astconf.SIP_FILE)
    os.makedirs(os.path.dirname(astconf.SIP_FILE), exist_ok=True)
    open(astconf.SIP_FILE, "w").write("; Fritzbox noch nicht eingerichtet\n")
    assert astconf.sync_fritz() == "none"
    assert open(astconf.SIP_FILE).read() == "; Fritzbox noch nicht eingerichtet\n"


def test_startup_reloads_pjsip_only_on_change(bridge):
    async def scenario(b):
        set_db("db-user", "db-pass")
        write_file("file-user", "file-pass")
        res, task = await astconf.startup()
        assert res == "written" and task
        await task
        assert b.ami.commands() == ["module reload res_pjsip.so"]
        res, task = await astconf.startup()
        assert res == "same" and task is None
        assert b.ami.commands() == ["module reload res_pjsip.so"]
    run(bridge, scenario)


def render(tmp_path, env_text=""):
    data = os.path.dirname(os.path.dirname(astconf.SIP_FILE))          # data/ (fritz.conf liegt in data/asterisk/)
    envf = tmp_path / "env"
    envf.write_text("AMI_SECRET=render-test\n" + env_text)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "asterisk", "render.py")], capture_output=True, text=True,
                       env=dict(os.environ, LM_ENV_FILE=str(envf), LM_CONF_DIR=str(tmp_path / "conf"), LM_DATA_DIR=data))
    assert r.returncode == 0, r.stderr
    return r


def test_render_never_overwrites_and_prefers_db(bridge, tmp_path):
    set_db("db-user", "db-pass")
    render(tmp_path, "FRITZ_USER=env-user\nFRITZ_PASSWORD=env-pass\nFRITZ_HOST=10.0.0.1\n")
    assert file_creds() == ("db-user", "db-pass", HOST)            # fehlte: aus der Datenbank (Vorrang vor .env)
    write_file("file-user", "file-pass")
    render(tmp_path)
    assert file_creds() == ("file-user", "file-pass", HOST)        # vorhandene Datei bleibt ...
    assert astconf.sync_fritz() == "written"                       # ... und der Start der Bridge setzt die DB durch
    assert file_creds() == ("db-user", "db-pass", HOST)


def test_render_without_db_uses_env_or_placeholder(bridge, tmp_path):
    render(tmp_path)
    assert open(astconf.SIP_FILE).read().startswith("; Fritzbox noch nicht eingerichtet")
    os.remove(astconf.SIP_FILE)
    render(tmp_path, "FRITZ_USER=env-user\nFRITZ_PASSWORD=env-pass\nFRITZ_HOST=10.0.0.1\n")
    assert file_creds() == ("env-user", "env-pass", "10.0.0.1")


def admin(fn):
    async def wrapped(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        main.app.dependency_overrides[main.user_auth] = lambda: main.PSEUDO_ADMIN
        try:
            await fn(b)
        finally:
            main.app.dependency_overrides.clear()
    return wrapped


def test_config_put_writes_file_and_reloads(bridge):
    @admin
    async def scenario(b):
        r = await b.http.put("/api/config", json={"fritz_user": "neu", "fritz_password": "neu-pass", "fritz_host": HOST})
        assert r.status_code == 200, r.text
        assert file_creds() == ("neu", "neu-pass", HOST)
        assert "module reload res_pjsip.so" in b.ami.commands()
    run(bridge, scenario)


def test_config_put_rejected_restores_previous(bridge):
    @admin
    async def scenario(b):
        set_db("alt", "alt-pass")
        write_file("alt", "alt-pass")
        b.ami.reg_status = "Rejected"
        seq = iter(["Rejected"] + ["Registered"] * 50)

        async def reg(name, timeout_s=10):
            return next(seq)
        main.ami.registration_status = reg
        r = await b.http.put("/api/config", json={"fritz_password": "falsch"})
        assert r.status_code == 502 and "wiederhergestellt" in r.text
        assert config.get("fritz_password") == "alt-pass"
        assert file_creds() == ("alt", "alt-pass", HOST)
    run(bridge, scenario)


def test_render_ignores_inline_comments(bridge, tmp_path):
    """Kommentar hinter einem Wert gehoert nicht zum Wert (sonst landet er in der Dialplan-URL zur Bridge)."""
    render(tmp_path, "WEB_PORT=8091   # anderer Port\n")
    ext = (tmp_path / "conf" / "extensions.conf").read_text()
    assert "http://127.0.0.1:8091/internal/" in ext and "anderer Port" not in ext


def test_env_example_has_no_comment_values():
    """Compose (compose-go, python-dotenv) liest 'KEY=   # Text' als Wert '# Text': Kommentare in .env.example nur in eigenen Zeilen."""
    for line in open(os.path.join(ROOT, ".env.example"), encoding="utf-8"):
        s = line.strip()
        if s and not s.startswith("#"):
            key, _, val = s.partition("=")
            assert "#" not in val, line
