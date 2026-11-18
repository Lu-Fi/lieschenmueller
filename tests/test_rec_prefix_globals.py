"""Vorwahl fuer aufgezeichnete ausgehende Gespraeche im Dialplan (Notbetrieb ohne Bridge): die konfigurierte Vorwahl (meta rec_prefix)
steht als Asterisk-Global REC_PREFIX in data/asterisk/globals.conf (render.py legt sie an, die Bridge schreibt sie beim Start und beim
Speichern) und wird zusaetzlich per AMI Setvar gesetzt. Frueher entfernte der Dialplan nur "000" - mit dem Standard *77 scheiterte der Anruf."""
import os

import pytest

from app import astconf, main
from tests.conftest import ROOT, run
from tests.test_settings import admin, render


def globals_text():
    return open(astconf.GLOBALS_FILE, encoding="utf-8").read()


def test_render_creates_globals_with_default_or_db_value(bridge, tmp_path):
    render(tmp_path)
    assert "REC_PREFIX=*77\n" in globals_text() and "[globals]" in globals_text()
    os.remove(astconf.GLOBALS_FILE)
    bridge.db.set_meta("rec_prefix", "#9")
    render(tmp_path)
    assert "REC_PREFIX=#9\n" in globals_text()
    bridge.db.set_meta("rec_prefix", "*1")
    render(tmp_path)
    assert "REC_PREFIX=#9\n" in globals_text()             # vorhandene Datei bleibt (die Bridge gleicht sie ab)


def test_template_strips_configured_prefix_not_000():
    tpl = open(os.path.join(ROOT, "asterisk", "templates", "extensions.conf.tpl"), encoding="utf-8").read()
    assert '#tryinclude "dyn/globals.conf"' in tpl
    assert '"${EXTEN:0:3}" = "000"' not in tpl
    assert "GLOBAL(REC_PREFIX)" in tpl and "${EXTEN:${LEN(${RP})}}" in tpl


def test_write_globals_and_validation(bridge):
    assert astconf.write_globals() is True
    assert "REC_PREFIX=*77\n" in globals_text()
    assert astconf.write_globals() is False                 # unveraendert: nicht neu schreiben
    bridge.db.set_meta("rec_prefix", "")
    assert astconf.write_globals() is True and "REC_PREFIX=\n" in globals_text()      # leer = keine Vorwahl
    with pytest.raises(ValueError):
        astconf.render_globals("*7;x")


def test_settings_put_prefix_writes_file_and_sets_global(bridge):
    @admin
    async def scenario(b):
        r = await b.http.put("/api/settings", json={"rec_prefix": "*88"})
        assert r.status_code == 200, r.text
        assert "REC_PREFIX=*88\n" in globals_text()
        sets = [c[2] for c in b.ami.calls if c[0] == "action" and c[1] == "Setvar"]
        assert {"Variable": "REC_PREFIX", "Value": "*88"} in sets and all("Channel" not in s for s in sets)
        n = len(b.ami.calls)
        r = await b.http.put("/api/settings", json={"rec_prefix": "*88"})       # unveraendert: kein erneutes Setvar
        assert r.status_code == 200 and len(b.ami.calls) == n
        r = await b.http.put("/api/settings", json={"rec_prefix": "*8;"})
        assert r.status_code == 400
    run(bridge, scenario)


def test_startup_sets_globals(bridge):
    async def scenario(b):
        b.db.set_meta("rec_prefix", "000")
        await astconf.startup_globals()
        assert "REC_PREFIX=000\n" in globals_text()
        assert ("action", "Setvar", {"Variable": "REC_PREFIX", "Value": "000"}) in b.ami.calls
    run(bridge, scenario)


def test_outcall_uses_same_prefix(bridge):
    """Die Bridge selbst liest dieselbe Einstellung (astconf.rec_prefix) - Dialplan und Bridge sind sich einig."""
    async def scenario(b):
        b.db.set_meta("rec_prefix", "*5")
        assert main.astconf.rec_prefix() == "*5"
        r = await b.internal("outcall", uuid="u-pfx", to="*50301234567", frm="99610", chan="PJSIP/99610-1")
        assert r.endswith("0301234567") and "*5" not in r
    run(bridge, scenario)
