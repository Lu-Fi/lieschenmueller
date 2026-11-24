"""Integration Lieschen: Einrichtung, Schalter, Sensor, Aktionen, Ereignisstrom (gegen einen kleinen Fake-Server)."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from aiohttp import web
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.lieschen.api import LieschenClient, LieschenError
from custom_components.lieschen.config_flow import parse_switches
from custom_components.lieschen.const import DOMAIN

INFO = {"devices": [{"name": "Headset 1", "number": "**610"}], "agent": "Assistent", "calls": []}
P = "custom_components.lieschen.api.LieschenClient."


async def test_config_flow(hass):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM
    with patch(P + "info", AsyncMock(side_effect=LieschenError(401, "falsch"))):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_URL: "192.168.1.5:8091", CONF_API_KEY: "x"})
    assert r["errors"] == {"base": "invalid_auth"}
    with patch(P + "info", AsyncMock(return_value=INFO)), patch(P + "listen", AsyncMock()):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_URL: "192.168.1.5:8091", CONF_API_KEY: " lm_k "})
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"] == {CONF_URL: "http://192.168.1.5:8091", CONF_API_KEY: "lm_k"}


def test_parse_switches():
    sw = parse_switches("Headset 1 Max | Headset 1 | 0171 123\n\nHeadset KI | **610 | ki")
    assert sw == [{"name": "Headset 1 Max", "device": "Headset 1", "target": "0171 123"},
                  {"name": "Headset KI", "device": "**610", "target": "KI"}]
    for bad in ("a | b", "a | b | c\nA | x | y", " | b | c"):
        try:
            parse_switches(bad)
            assert False, bad
        except ValueError:
            pass


async def _setup(hass, switches, **opts):
    cb = {}

    async def listen(self, on_snapshot, on_call, on_state):
        cb.update(snap=on_snapshot, call=on_call, state=on_state)
        on_state(True)
        await asyncio.Event().wait()

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: "http://lieschen:8091", CONF_API_KEY: "lm_k"},
                            options={"switches": switches, **opts})
    entry.add_to_hass(hass)
    with patch(P + "info", AsyncMock(return_value=INFO)), patch.object(LieschenClient, "listen", listen):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, cb


async def test_switch_connect_and_ki(hass):
    entry, cb = await _setup(hass, [{"name": "Headset 1 Max Handy", "device": "Headset 1", "target": "0171123"},
                                    {"name": "Headset 1 KI", "device": "Headset 1", "target": "KI"}], switch_mode="gespraech")
    sw, ki, sensor = "switch.lieschen_headset_1_max_handy", "switch.lieschen_headset_1_ki", "sensor.lieschen_anruf"
    assert hass.states.get(sw).state == "off" and hass.states.get(sensor).state == "bereit"
    events = async_capture_events(hass, "lieschen_call")
    conn = AsyncMock(return_value={"call_id": 7, "device": "**610", "target": "0171123", "state": "klingelt"})
    with patch(P + "connect", conn):
        await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    conn.assert_awaited_once_with("Headset 1", "0171123")
    assert hass.states.get(sw).state == "on"
    hang = AsyncMock(return_value={})
    with patch(P + "hangup", hang):
        # aus, waehrend es klingelt: erst nach dem Abheben auflegen
        await hass.services.async_call("switch", "turn_off", {"entity_id": sw}, blocking=True)
        assert hang.await_count == 0
        base = {"call_id": 7, "mode": "verbinden", "device": "**610", "target": "0171123", "reason": ""}
        cb["call"]({**base, "state": "abgenommen"})
        await hass.async_block_till_done()
        hang.assert_awaited_once_with(7)
    cb["call"]({**base, "state": "beendet"})
    await hass.async_block_till_done()
    assert hass.states.get(sw).state == "off"
    assert hass.states.get(sensor).state == "beendet"
    assert [e.data["state"] for e in events] == ["abgenommen", "beendet"]
    # KI-Schalter
    assist = AsyncMock(return_value={"call_id": 8, "device": "**610", "state": "klingelt"})
    with patch(P + "assistant", assist):
        await hass.services.async_call("switch", "turn_on", {"entity_id": ki}, blocking=True)
    assist.assert_awaited_once_with("Headset 1")
    cb["call"]({"call_id": 8, "mode": "ki", "device": "**610", "target": "KI", "state": "ki", "reason": ""})
    await hass.async_block_till_done()
    assert hass.states.get(ki).state == "on" and hass.states.get(sensor).state == "ki"
    # Fehler von Lieschen kommt als Fehlermeldung an
    with patch(P + "connect", AsyncMock(side_effect=LieschenError(409, "Mehrere passen: A, B"))):
        try:
            await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
            assert False
        except Exception as e:
            assert "Mehrere passen" in str(e)
    # Ereignisstrom weg: Entitaeten nicht verfuegbar
    cb["state"](False)
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "unavailable"


async def test_services_with_response(hass):
    await _setup(hass, [])
    with patch(P + "connect", AsyncMock(return_value={"call_id": 3, "state": "klingelt"})):
        r = await hass.services.async_call(DOMAIN, "verbinden", {"device": "Headset 1", "target": "Max"}, blocking=True,
                                           return_response=True)
    assert r == {"call_id": 3, "state": "klingelt"}
    with patch(P + "hangup", AsyncMock(side_effect=LieschenError(409, "Das Telefon klingelt noch"))):
        try:
            await hass.services.async_call(DOMAIN, "auflegen", {"device": "Headset 1"}, blocking=True)
            assert False
        except Exception as e:
            assert "klingelt noch" in str(e)


async def test_client_against_server(socket_enabled, aiohttp_client):
    """Echter HTTP-Weg: Schluessel im Header, Fehlertext aus detail, Ereignisstrom mit snapshot/data/ping/bye."""
    seen = []

    async def connect(req):
        seen.append(req.headers.get("Authorization"))
        body = await req.json()
        if body["target"] == "Niemand":
            return web.json_response({"detail": "Niemanden mit diesem Namen gefunden"}, status=404)
        return web.json_response({"call_id": 1, "state": "klingelt"})

    async def events(req):
        r = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await r.prepare(req)
        await r.write(b'event: snapshot\ndata: []\n\n')
        await r.write(b'event: ping\ndata: 1\n\n')
        await r.write(b'data: ' + json.dumps({"call_id": 1, "state": "verbunden"}).encode() + b'\n\n')
        await r.write(b'event: bye\ndata: 1\n\n')
        return r

    app = web.Application()
    app.router.add_post("/api/voice/connect", connect)
    app.router.add_get("/api/voice/events", events)
    client = await aiohttp_client(app)
    cl = LieschenClient(client.session, str(client.make_url("")), "lm_k")
    assert (await cl.connect("**610", "Mama"))["call_id"] == 1 and seen == ["Bearer lm_k"]
    try:
        await cl.connect("**610", "Niemand")
        assert False
    except LieschenError as e:
        assert e.status == 404 and e.text == "Niemanden mit diesem Namen gefunden"
    got, states = [], []
    task = asyncio.create_task(cl.listen(lambda s: got.append(("snap", s)), lambda c: got.append(("call", c)), states.append))
    for _ in range(50):
        if states[-1:] == [False]:
            break
        await asyncio.sleep(0.05)
    task.cancel()
    assert got == [("snap", []), ("call", {"call_id": 1, "state": "verbunden"})]
    assert states[:2] == [True, False]


async def test_options_menu_with_lists(hass):
    info = dict(INFO, devices=[{"name": "Headset 1", "number": "**610"}, {"name": "Küche", "number": "**611"}],
                targets=[{"value": "Max Muster", "label": "Max Muster (alle Nummern)", "kind": "benutzer", "name": "Max Muster"},
                         {"value": "01711234567", "label": "Max (Handy): 01711234567", "kind": "kontakt", "name": "Max Handy"}])
    entry, _ = await _setup(hass, [])
    with patch(P + "info", AsyncMock(return_value=info)):
        r = await hass.config_entries.options.async_init(entry.entry_id)
        assert r["type"] is FlowResultType.MENU and "remove" not in r["menu_options"]
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "add"})
        assert r["type"] is FlowResultType.FORM and r["step_id"] == "add"
        opts = r["data_schema"].schema["target"].config["options"]
        assert opts[0]["value"] == "KI" and {"value": "01711234567", "label": "Max (Handy): 01711234567"} in opts
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"device": "**610", "target": "01711234567"})
        assert r["type"] is FlowResultType.MENU and "Headset 1 Max Handy:" in r["description_placeholders"]["switches"]
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "add"})
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"device": "**610", "target": "KI", "name": "Headset KI"})
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "add"})
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"device": "**611", "target": "KI", "name": "headset ki"})
        assert r["errors"] == {"name": "duplicate"}
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"device": "**611", "target": "Max Muster", "name": ""})
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "remove"})
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"remove": ["Headset KI"]})
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "finish"})
        await hass.async_block_till_done()
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["switch_mode"] == "taster" and entry.options["min_on"] == 3
    assert entry.options["switches"] == [
        {"name": "Headset 1 Max Handy", "device": "**610", "target": "01711234567"},
        {"name": "Küche Max Muster", "device": "**611", "target": "Max Muster"}]


async def test_switch_pulse_for_alexa(hass):
    """Standard "taster": an fuer min_on Sekunden, dann aus, auch wenn das Gespraech weiterlaeuft; Fehler nur an Sensor/Ereignis."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    entry, cb = await _setup(hass, [{"name": "Oma", "device": "**612", "target": "Oma"}], min_on=2)
    sw, sensor = "switch.lieschen_oma", "sensor.lieschen_anruf"
    events = async_capture_events(hass, "lieschen_call")
    with patch(P + "connect", AsyncMock(return_value={"call_id": 9, "state": "klingelt"})) as conn:
        await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
        assert hass.states.get(sw).state == "on"
        cb["call"]({"call_id": 9, "mode": "verbinden", "device": "**612", "target": "Oma", "state": "verbunden", "reason": ""})
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
        await hass.async_block_till_done()
        assert hass.states.get(sw).state == "off"            # Gespraech laeuft, Schalter ist trotzdem wieder aus
        assert hass.states.get(sw).attributes["call_state"] == "verbunden"
        # nochmal einschalten, waehrend das Gespraech laeuft: kein zweiter Anruf, nur der Puls
        await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
        assert conn.await_count == 1 and hass.states.get(sw).state == "on"
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()
    cb["call"]({"call_id": 9, "mode": "verbinden", "device": "**612", "target": "Oma", "state": "beendet", "reason": ""})
    await hass.async_block_till_done()
    # Fehler: kein Fehler an Alexa, aber Sensor "fehler" und Ereignis
    with patch(P + "connect", AsyncMock(side_effect=LieschenError(404, "Niemanden mit diesem Namen gefunden"))):
        await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    assert hass.states.get(sw).state == "on"
    assert hass.states.get(sensor).state == "fehler"
    assert hass.states.get(sensor).attributes["reason"] == "Niemanden mit diesem Namen gefunden"
    assert events[-1].data["state"] == "fehler" and events[-1].data["switch"] == sw
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done()
    assert hass.states.get(sw).state == "off"


async def test_removed_switch_leaves_registry(hass):
    from homeassistant.helpers import entity_registry as er

    entry, _ = await _setup(hass, [{"name": "Mobil KI", "device": "**610", "target": "KI"}])
    reg = er.async_get(hass)
    assert reg.async_get("switch.lieschen_mobil_ki")
    with patch(P + "info", AsyncMock(return_value=INFO)):
        hass.config_entries.async_update_entry(entry, options={"switches": []})
        await hass.async_block_till_done()
    assert reg.async_get("switch.lieschen_mobil_ki") is None
    assert reg.async_get("sensor.lieschen_anruf")
