"""Schalter je Verbindung aus den Optionen: einschalten = Anruf starten (Geraet klingelt). Ziel "KI": nach dem Abheben
meldet sich die KI und verbindet auf Wunsch.

Verhalten (Optionen > Einstellungen):
  taster    (Standard) an fuer mindestens min_on Sekunden, dann von selbst aus, wie ein Taster. So bemerkt Alexa das
            Einschalten sicher, und der naechste Sprachbefehl findet den Schalter wieder aus. Ein Fehler (z. B. Ziel nicht
            gefunden) geht nicht an Alexa, sondern an den Sensor "Anruf" (Stand "fehler") und das Ereignis lieschen_call.
  gespraech an, solange das Gespraech laeuft (mindestens min_on Sekunden), aus = auflegen; aus, waehrend das Telefon noch
            klingelt: Lieschen legt auf, sobald abgenommen wird (vorher kennt Asterisk den Kanal nicht)."""
from __future__ import annotations

import logging
import re
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .api import LieschenError
from .const import CONF_MIN_ON, CONF_MODE, CONF_SWITCHES, DEFAULT_MIN_ON, EVENT_CALL, FINAL, KI, MODE_CALL, MODE_PULSE
from .entity import LieschenEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    sw = [LieschenSwitch(entry, s) for s in entry.options.get(CONF_SWITCHES, [])]
    # in den Optionen entfernte Schalter auch aus der Entitaetsliste nehmen (sonst bleiben sie als "nicht verfuegbar" stehen)
    keep = {x.unique_id for x in sw}
    reg = er.async_get(hass)
    for e in er.async_entries_for_config_entry(reg, entry.entry_id):
        if e.domain == "switch" and e.unique_id.startswith(f"{entry.entry_id}_sw_") and e.unique_id not in keep:
            reg.async_remove(e.entity_id)
    async_add_entities(sw)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "schalter"


class LieschenSwitch(LieschenEntity, SwitchEntity):
    _attr_icon = "mdi:phone-forward"

    def __init__(self, entry, cfg: dict) -> None:
        super().__init__(entry)
        self._cfg = cfg
        self._mode = entry.options.get(CONF_MODE, MODE_PULSE)
        self._min_on = float(entry.options.get(CONF_MIN_ON, DEFAULT_MIN_ON))
        self._attr_name = cfg["name"]
        self._attr_unique_id = f"{entry.entry_id}_sw_{_slug(cfg['name'])}"
        if cfg["target"] == KI:
            self._attr_icon = "mdi:robot-outline"
        self._call_id: int | None = None
        self._off_pending = False
        self._pulse = False
        self._unsub_pulse = None
        self._error = ""

    @property
    def _call(self) -> dict | None:
        return self._data.calls.get(self._call_id) if self._call_id else None

    @property
    def _running(self) -> bool:
        c = self._call
        return bool(c and c.get("state") not in FINAL)

    @property
    def available(self) -> bool:
        return self._data.connected or self._pulse

    @property
    def is_on(self) -> bool:
        if self._pulse:
            return True
        return self._mode == MODE_CALL and self._running

    @property
    def extra_state_attributes(self) -> dict:
        c = self._call or {}
        return {"device": self._cfg["device"], "target": self._cfg["target"], "mode": self._mode, "call_id": c.get("call_id"),
                "answered": c.get("answered"), "call_state": c.get("state"), "reason": c.get("reason") or self._error or None}

    @callback
    def _release(self, _now=None) -> None:
        self._pulse = False
        self._unsub_pulse = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_pulse:
            self._unsub_pulse()

    async def async_turn_on(self, **kwargs) -> None:
        if self.is_on and self._running:
            return
        # erst sichtbar einschalten (Alexa), nach min_on wieder pruefen
        self._pulse = True
        self._error = ""
        if self._unsub_pulse:
            self._unsub_pulse()
        self._unsub_pulse = async_call_later(self.hass, self._min_on, self._release)
        self.async_write_ha_state()
        if self._running:                         # Gespraech laeuft noch: kein zweiter Anruf
            return
        cl = self._data.client
        try:
            r = await (cl.assistant(self._cfg["device"]) if self._cfg["target"] == KI else cl.connect(self._cfg["device"], self._cfg["target"]))
        except LieschenError as e:
            self._error = e.text
            _LOGGER.warning("Lieschen-Schalter %s: %s", self._cfg["name"], e.text)
            err = {"call_id": None, "mode": "ki" if self._cfg["target"] == KI else "verbinden", "device": self._cfg["device"],
                   "target": self._cfg["target"], "state": "fehler", "reason": e.text}
            self._data.last = err
            self.hass.bus.async_fire(EVENT_CALL, {**err, "config_entry_id": self._entry.entry_id, "switch": self.entity_id})
            self._signal()
            if self._mode == MODE_CALL:              # Fehler gleich zeigen (der Schalter bleibt aus)
                if self._unsub_pulse:
                    self._unsub_pulse()
                self._release()
                raise HomeAssistantError(f"Lieschen: {e.text}") from e
            return
        self._call_id = r["call_id"]
        self._off_pending = False
        self._data.calls.setdefault(self._call_id, {"call_id": self._call_id, "state": r.get("state", "klingelt"), "reason": ""})
        self.async_write_ha_state()

    def _signal(self) -> None:
        from homeassistant.helpers.dispatcher import async_dispatcher_send

        from .const import SIGNAL_UPDATE
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self._entry.entry_id))

    async def async_turn_off(self, **kwargs) -> None:
        if self._unsub_pulse:
            self._unsub_pulse()
            self._unsub_pulse = None
        self._pulse = False
        if not self._running:
            self.async_write_ha_state()
            return
        if self._call.get("state") == "klingelt":
            self._off_pending = True          # auflegen, sobald abgenommen wurde
            self.async_write_ha_state()
            return
        await self._hangup()
        self.async_write_ha_state()

    async def _hangup(self) -> None:
        self._off_pending = False
        try:
            await self._data.client.hangup(self._call_id)
        except LieschenError as e:
            if e.status != 404:
                raise HomeAssistantError(f"Lieschen: {e.text}") from e

    @callback
    def _updated(self) -> None:
        c = self._call
        if self._off_pending and c and c.get("state") not in FINAL and c.get("state") != "klingelt":
            self.hass.async_create_task(self._hangup())
        self.async_write_ha_state()
