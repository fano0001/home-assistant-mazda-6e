"""Lock platform for Mazda 6e vehicle doors."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up door lock entities for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        Mazda6eDoorLock(coordinator, item["vehicle"])
        for item in coordinator.data.values()
    )


class Mazda6eDoorLock(CoordinatorEntity, LockEntity):
    """Represent the Mazda 6e door locks."""
    _attr_has_entity_name = True
    _attr_translation_key = "doors"

    def __init__(self, coordinator, vehicle) -> None:
        """Initialize a vehicle door lock entity."""
        super().__init__(coordinator)
        self.vehicle = vehicle
        self._attr_unique_id = f"{vehicle.vehicle_id}_door_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(vehicle.vehicle_id))},
            name=f"Mazda 6e - {vehicle.vehicle_id}",
            serial_number=vehicle.vin,
            manufacturer="Mazda",
            model="6e",
        )

    @property
    def is_locked(self) -> bool | None:
        """Return whether all reported door locks are locked."""
        item = self.coordinator.data.get(self.vehicle.vehicle_id)
        try:
            door = item["status"]["door"]
            driver = door["driverLock"]
            passenger = door["passengerLock"]
        except (KeyError, TypeError):
            return None
        if driver == 0 and passenger == 0:
            return True
        if driver == 1 and passenger == 1:
            return False
        return None

    @property
    def available(self) -> bool:
        """Return whether cloud-control credentials are configured."""
        return (
            super().available
            and bool(self.coordinator.api.control_private_key)
            and bool(self.coordinator.api.control_pin)
        )

    async def async_lock(self, **kwargs) -> None:
        """Lock the vehicle doors."""
        await self.coordinator.api.async_lock(self.vehicle.vehicle_id)
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs) -> None:
        """Unlock the vehicle doors."""
        await self.coordinator.api.async_unlock(self.vehicle.vehicle_id)
        await self.coordinator.async_request_refresh()
