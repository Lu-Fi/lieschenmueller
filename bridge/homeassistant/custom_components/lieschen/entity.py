"""Gemeinsame Basis: ein Geraet "Lieschen" je Eintrag, Aktualisierung ueber den Ereignisstrom."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATE


class LieschenEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry) -> None:
        self._entry = entry
        self._data = entry.runtime_data
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Lieschen",
                                            manufacturer="LieschenMueller", configuration_url=self._data.client.url)

    @property
    def available(self) -> bool:
        return self._data.connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE.format(self._entry.entry_id), self._updated))

    def _updated(self) -> None:
        self.async_write_ha_state()
