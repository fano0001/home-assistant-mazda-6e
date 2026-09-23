"""Cover controls for Mazda 6e windows."""

from homeassistant.components.cover import CoverDeviceClass, CoverEntity, CoverEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MazdaApiError
from .const import DOMAIN
from .entity import Mazda6eEntity

WINDOWS_DESCRIPTION = CoverEntityDescription(
    key="windows",
    translation_key="windows",
    device_class=CoverDeviceClass.WINDOW,
    icon="mdi:car-door",
)

def _supports(vehicle, *function_codes: str) -> bool:
    return not vehicle.functions or any(code in vehicle.functions for code in function_codes)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up window covers where vehicle status supports them."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []

    for item in coordinator.data.values():
        vehicle = item["vehicle"]
        status = item.get("status") or {}
        if "window" in status and _supports(vehicle, "WindowSW", "WindowSlightlyDown"):
            entities.append(Mazda6eWindowsCover(coordinator, vehicle))

    async_add_entities(entities)


class _Mazda6eCover(Mazda6eEntity, CoverEntity):
    """Base class for vehicle cloud-control covers."""

    @property
    def available(self) -> bool:
        return (
            super().available
            and bool(self.coordinator.api.control_private_key)
            and bool(self.coordinator.api.control_pin)
        )


class Mazda6eWindowsCover(_Mazda6eCover):
    """Represent all vehicle windows as one cover."""

    def __init__(self, coordinator, vehicle) -> None:
        super().__init__(coordinator, vehicle, WINDOWS_DESCRIPTION)

    @property
    def is_closed(self) -> bool | None:
        try:
            return not any(self.vehicle_data["status"]["window"]["windows"])
        except (KeyError, TypeError):
            return None

    async def async_open_cover(self, **kwargs) -> None:
        await self._async_set_windows(True)

    async def async_close_cover(self, **kwargs) -> None:
        await self._async_set_windows(False)

    async def _async_set_windows(self, open_windows: bool) -> None:
        try:
            await self.coordinator.api.async_set_windows(self.vehicle.vehicle_id, open_windows)
        except (MazdaApiError, RuntimeError, TimeoutError) as err:
            raise HomeAssistantError(f"Mazda rejected the window command: {err}") from err
        await self.coordinator.async_request_refresh()


# Trunk control is exposed through the lock platform.
