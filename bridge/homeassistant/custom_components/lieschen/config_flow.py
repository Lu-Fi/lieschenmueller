"""Einrichtung ueber die Oberflaeche: Adresse und Schluessel; Optionen: Schalter (Name | Geraet | Ziel oder KI)."""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (NumberSelector, NumberSelectorConfig, NumberSelectorMode, SelectOptionDict, SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector,
                                            TextSelectorConfig, TextSelectorType)

from .api import LieschenClient, LieschenError
from .const import CONF_MIN_ON, CONF_MODE, CONF_SWITCHES, DEFAULT_MIN_ON, DOMAIN, KI, MODE_CALL, MODE_PULSE


async def _check(hass, url: str, key: str) -> tuple[dict | None, str | None]:
    try:
        return await LieschenClient(async_get_clientsession(hass), url, key).info(), None
    except LieschenError as e:
        return None, {0: "cannot_connect", 401: "invalid_auth", 403: "disabled"}.get(e.status, "unknown")


def parse_switches(text: str) -> list[dict]:
    """Je Zeile "Name | Geraet | Ziel"; Ziel "KI" = die KI meldet sich und verbindet auf Wunsch. ValueError mit Zeile."""
    out, seen = [], set()
    for n, line in enumerate((text or "").splitlines(), 1):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(n)
        if parts[0].casefold() in seen:
            raise ValueError(n)
        seen.add(parts[0].casefold())
        out.append({"name": parts[0], "device": parts[1], "target": KI if parts[2].casefold() == "ki" else parts[2]})
    return out


def switches_text(sw: list[dict]) -> str:
    return "\n".join(f"{s['name']} | {s['device']} | {s['target']}" for s in sw)


class LieschenConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            if not re.match(r"https?://", url):
                url = "http://" + url
            key = user_input[CONF_API_KEY].strip()
            _, err = await _check(self.hass, url, key)
            if err:
                errors["base"] = err
            else:
                await self.async_set_unique_id(url.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Lieschen", data={CONF_URL: url, CONF_API_KEY: key},
                                               options={CONF_SWITCHES: []})
        schema = vol.Schema({
            vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "http://")): str,
            vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            key = (user_input.get(CONF_API_KEY) or "").strip() or entry.data[CONF_API_KEY]
            _, err = await _check(self.hass, url, key)
            if err:
                errors["base"] = err
            else:
                return self.async_update_reload_and_abort(entry, data={CONF_URL: url, CONF_API_KEY: key})
        schema = vol.Schema({
            vol.Required(CONF_URL, default=entry.data[CONF_URL]): str,
            vol.Optional(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        })
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LieschenOptionsFlow()


class LieschenOptionsFlow(OptionsFlow):
    """Schalter zusammenstellen: Geraet und Ziel aus den Listen von Lieschen waehlen (eigene Eingabe geht auch)."""

    def __init__(self) -> None:
        self._sw: list[dict] | None = None
        self._info: dict | None = None
        self._opts: dict | None = None

    def _list(self) -> str:
        return "\n".join(f"- {s['name']}: {s['device']} → {s['target']}" for s in self._sw) or "(noch keine)"

    async def _load(self) -> dict:
        if self._info is None:
            rd = getattr(self.config_entry, "runtime_data", None)
            try:
                self._info = await rd.client.info() if rd else {}
            except LieschenError:
                self._info = {}
        return self._info

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._sw is None:
            self._sw = [dict(s) for s in self.config_entry.options.get(CONF_SWITCHES, [])]
        if self._opts is None:
            self._opts = {CONF_MODE: self.config_entry.options.get(CONF_MODE, MODE_PULSE),
                          CONF_MIN_ON: self.config_entry.options.get(CONF_MIN_ON, DEFAULT_MIN_ON)}
        menu = (["add", "remove"] if self._sw else ["add"]) + ["text", "settings", "finish"]
        return self.async_show_menu(step_id="init", menu_options=menu, description_placeholders={"switches": self._list()})

    async def async_step_add(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        info = await self._load()
        devs = info.get("devices") or []
        tgts = info.get("targets") or []
        dlabel = {d["number"]: (f"{d['name']} ({d['number']})" if d["name"] != d["number"] else d["number"]) for d in devs}
        errors: dict[str, str] = {}
        if user_input is not None:
            dev, tgt = user_input["device"].strip(), user_input["target"].strip()
            if tgt.casefold() == "ki":
                tgt = KI
            dn = next((d["name"] for d in devs if d["number"] == dev), dev)
            tn = "KI" if tgt == KI else next((t.get("name") or t["label"] for t in tgts if t["value"] == tgt), tgt)
            name = (user_input.get("name") or "").strip() or f"{dn} {tn}"[:60]
            if not dev or not tgt:
                errors["base"] = "missing"
            elif any(s["name"].casefold() == name.casefold() for s in self._sw):
                errors["name"] = "duplicate"
            else:
                self._sw.append({"name": name, "device": dev, "target": tgt})
                return await self.async_step_init()
        schema = vol.Schema({
            vol.Required("device"): SelectSelector(SelectSelectorConfig(
                options=[SelectOptionDict(value=k, label=v) for k, v in dlabel.items()], custom_value=True,
                mode=SelectSelectorMode.DROPDOWN)),
            vol.Required("target"): SelectSelector(SelectSelectorConfig(
                options=[SelectOptionDict(value=KI, label="KI – meldet sich und verbindet auf Wunsch")]
                + [SelectOptionDict(value=t["value"], label=t["label"]) for t in tgts],
                custom_value=True, mode=SelectSelectorMode.DROPDOWN)),
            vol.Optional("name"): str,
        })
        return self.async_show_form(step_id="add", data_schema=schema, errors=errors,
                                    description_placeholders={"switches": self._list()})

    async def async_step_remove(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            drop = set(user_input.get("remove") or [])
            self._sw = [s for s in self._sw if s["name"] not in drop]
            return await self.async_step_init()
        schema = vol.Schema({vol.Optional("remove", default=[]): SelectSelector(SelectSelectorConfig(
            options=[s["name"] for s in self._sw], multiple=True, mode=SelectSelectorMode.LIST))})
        return self.async_show_form(step_id="remove", data_schema=schema)

    async def async_step_text(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Alle Schalter als Text (je Zeile Name | Geraet | Ziel), z. B. zum schnellen Kopieren."""
        errors: dict[str, str] = {}
        placeholders = {"line": ""}
        text = switches_text(self._sw)
        if user_input is not None:
            text = user_input.get(CONF_SWITCHES, "")
            try:
                self._sw = parse_switches(text)
            except ValueError as e:
                errors[CONF_SWITCHES] = "bad_line"
                placeholders["line"] = str(e.args[0])
            else:
                return await self.async_step_init()
        schema = vol.Schema({vol.Optional(CONF_SWITCHES, description={"suggested_value": text}):
                             TextSelector(TextSelectorConfig(multiline=True))})
        return self.async_show_form(step_id="text", data_schema=schema, errors=errors, description_placeholders=placeholders)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._opts = {CONF_MODE: user_input[CONF_MODE], CONF_MIN_ON: int(user_input[CONF_MIN_ON])}
            return await self.async_step_init()
        schema = vol.Schema({
            vol.Required(CONF_MODE, default=self._opts[CONF_MODE]): SelectSelector(SelectSelectorConfig(
                options=[MODE_PULSE, MODE_CALL], translation_key="switch_mode", mode=SelectSelectorMode.LIST)),
            vol.Required(CONF_MIN_ON, default=self._opts[CONF_MIN_ON]): NumberSelector(NumberSelectorConfig(
                min=1, max=30, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="s")),
        })
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(data={CONF_SWITCHES: self._sw or [], **(self._opts or {})})
