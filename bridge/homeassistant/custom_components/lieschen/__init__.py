"""Lieschen Telefonassistent: Anrufe aus Home Assistant starten (verbinden oder KI) und ihren Stand verfolgen.

Spricht die Sprachbefehl-API von Lieschen an (Schluessel aus Lieschen > Einstellungen > Anbindungen > Sprachbefehle).
Ereignisse kommen per Server-Sent Events (/api/voice/events), Home Assistant muss nicht nachfragen."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from .api import LieschenClient, LieschenError
from .const import DOMAIN, EVENT_CALL, SIGNAL_UPDATE
from .data import LieschenData

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type LieschenConfigEntry = ConfigEntry[LieschenData]

SCHEMA_CONNECT = vol.Schema({vol.Required("device"): cv.string, vol.Required("target"): cv.string,
                             vol.Optional("config_entry_id"): cv.string})
SCHEMA_ASSIST = vol.Schema({vol.Required("device"): cv.string, vol.Optional("config_entry_id"): cv.string})
SCHEMA_HANGUP = vol.Schema({vol.Optional("call_id"): cv.positive_int, vol.Optional("device"): cv.string,
                            vol.Optional("config_entry_id"): cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    def _data(call: ServiceCall) -> LieschenData:
        entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)
                   if not call.data.get("config_entry_id") or e.entry_id == call.data["config_entry_id"]]
        if not entries:
            raise ServiceValidationError("Lieschen ist nicht eingerichtet")
        return entries[0].runtime_data

    async def _run(coro) -> dict:
        try:
            return await coro
        except LieschenError as e:
            raise HomeAssistantError(f"Lieschen: {e.text}") from e

    async def connect(call: ServiceCall) -> ServiceResponse:
        d = _data(call)
        return await _run(d.client.connect(call.data["device"], call.data["target"]))

    async def assistant(call: ServiceCall) -> ServiceResponse:
        d = _data(call)
        return await _run(d.client.assistant(call.data["device"]))

    async def hangup(call: ServiceCall) -> ServiceResponse:
        if not call.data.get("call_id") and not call.data.get("device"):
            raise ServiceValidationError("call_id oder device angeben")
        d = _data(call)
        return await _run(d.client.hangup(call.data.get("call_id"), call.data.get("device")))

    hass.services.async_register(DOMAIN, "verbinden", connect, SCHEMA_CONNECT, SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "assistent", assistant, SCHEMA_ASSIST, SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "auflegen", hangup, SCHEMA_HANGUP, SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LieschenConfigEntry) -> bool:
    client = LieschenClient(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_API_KEY])
    try:
        info = await client.info()
    except LieschenError as e:
        raise ConfigEntryNotReady(e.text) from e
    data = LieschenData(client, devices=info.get("devices") or [], agent=info.get("agent") or "")
    entry.runtime_data = data
    signal = SIGNAL_UPDATE.format(entry.entry_id)

    @callback
    def on_snapshot(calls: list) -> None:
        data.calls = {c["call_id"]: c for c in calls}
        if calls:
            data.last = calls[-1]
        async_dispatcher_send(hass, signal)

    @callback
    def on_call(c: dict) -> None:
        old = data.calls.get(c["call_id"])
        data.calls[c["call_id"]] = c
        data.last = c
        if not old or old.get("state") != c.get("state") or old.get("reason") != c.get("reason"):
            hass.bus.async_fire(EVENT_CALL, {**c, "config_entry_id": entry.entry_id})
        async_dispatcher_send(hass, signal)

    @callback
    def on_state(up: bool) -> None:
        if data.connected != up:
            data.connected = up
            async_dispatcher_send(hass, signal)

    on_snapshot(info.get("calls") or [])
    entry.async_create_background_task(hass, client.listen(on_snapshot, on_call, on_state), "lieschen_events")
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _reload(hass: HomeAssistant, entry: LieschenConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LieschenConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
