"""Sensor "Letzter Anruf": Stand des zuletzt gestarteten oder geaenderten Sprachbefehl-Anrufs."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import callback

from .entity import LieschenEntity

STATES = ["bereit", "klingelt", "abgenommen", "ki", "verbunden", "beendet", "nicht_erreicht", "fehler"]


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([LieschenCallSensor(entry)])


class LieschenCallSensor(LieschenEntity, SensorEntity):
    _attr_translation_key = "anruf"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATES

    def __init__(self, entry) -> None:
        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_anruf"

    @property
    def native_value(self) -> str:
        last = self._data.last
        return last["state"] if last and last.get("state") in STATES else "bereit"

    @property
    def extra_state_attributes(self) -> dict:
        last = self._data.last or {}
        return {k: last.get(k) for k in ("call_id", "mode", "device", "answered", "target", "reason")}

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()
