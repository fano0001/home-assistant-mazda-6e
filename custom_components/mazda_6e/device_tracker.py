from __future__ import annotations

import logging

from homeassistant.components.device_tracker import (
    SourceType,
    TrackerEntity,
    TrackerEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import Mazda6eEntity
from .models import Mazda6eVehicle

_LOGGER = logging.getLogger(__name__)

DESCRIPTION = TrackerEntityDescription(
    key="location",
    translation_key="location",
    icon="mdi:car",
)

# the API has not been observed with location enabled, so accept the common spellings
_LATITUDE_KEYS = ("latitude", "lat", "gpsLatitude")
_LONGITUDE_KEYS = ("longitude", "lng", "lon", "gpsLongitude")


def _coordinate(location: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = location.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []

    for data in coordinator.data.values():
        location = (data.get("status") or {}).get("location")
        _LOGGER.debug("location payload: %s", location)
        if not isinstance(location, dict):
            continue
        if _coordinate(location, _LATITUDE_KEYS) is None:
            continue
        if _coordinate(location, _LONGITUDE_KEYS) is None:
            continue
        entities.append(Mazda6eDeviceTracker(coordinator, data["vehicle"], DESCRIPTION))

    async_add_entities(entities)


class Mazda6eDeviceTracker(Mazda6eEntity, TrackerEntity):
    """Reports the last known position of the vehicle."""

    _attr_source_type = SourceType.GPS

    @property
    def _location(self) -> dict:
        data = self.vehicle_data or {}
        location = (data.get("status") or {}).get("location")
        return location if isinstance(location, dict) else {}

    @property
    def latitude(self) -> float | None:
        return _coordinate(self._location, _LATITUDE_KEYS)

    @property
    def longitude(self) -> float | None:
        return _coordinate(self._location, _LONGITUDE_KEYS)

    @property
    def extra_state_attributes(self) -> dict:
        return self.vehicle_attributes
