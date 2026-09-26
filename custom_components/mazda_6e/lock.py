"""Lock platform for Mazda 6e vehicle doors."""

from __future__ import annotations

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MazdaApiError
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up door and trunk lock entities for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for item in coordinator.data.values():
        vehicle = item["vehicle"]
        status = item.get("status") or {}
        entities.append(Mazda6eDoorLock(coordinator, vehicle))
        if "door" in status and (not vehicle.functions or {"TrunkAutoSW", "TrunkUnlock"} & vehicle.functions):
            entities.append(Mazda6eTrunkLock(coordinator, vehicle))
    async_add_entities(entities)


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
        await self.coordinator.async_refresh_until(lambda: self.is_locked is True)

    async def async_unlock(self, **kwargs) -> None:
        """Unlock the vehicle doors."""
        await self.coordinator.api.async_unlock(self.vehicle.vehicle_id)
        await self.coordinator.async_refresh_until(lambda: self.is_locked is False)


class Mazda6eTrunkLock(CoordinatorEntity, LockEntity):
    """Represent the trunk as a lock-style open/closed control."""

    _attr_has_entity_name = True
    _attr_translation_key = "trunk"
    _attr_icon = "mdi:car-back"

    def __init__(self, coordinator, vehicle) -> None:
        super().__init__(coordinator)
        self.vehicle = vehicle
        self._attr_unique_id = f"{vehicle.vehicle_id}_trunk_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(vehicle.vehicle_id))},
            name=f"Mazda 6e - {vehicle.vehicle_id}",
            serial_number=vehicle.vin,
            manufacturer="Mazda",
            model="6e",
        )

    @property
    def is_locked(self) -> bool | None:
        """Return whether the trunk is closed."""
        item = self.coordinator.data.get(self.vehicle.vehicle_id)
        try:
            return not bool(item["status"]["door"]["trunk"])
        except (KeyError, TypeError):
            return None

    @property
    def available(self) -> bool:
        return (
            super().available
            and bool(self.coordinator.api.control_private_key)
            and bool(self.coordinator.api.control_pin)
        )

    async def async_lock(self, **kwargs) -> None:
        """Close the trunk."""
        await self._async_set_trunk(False)

    async def async_unlock(self, **kwargs) -> None:
        """Open the trunk."""
        await self._async_set_trunk(True)

    async def _async_set_trunk(self, open_trunk: bool) -> None:
        try:
            await self.coordinator.api.async_set_trunk(self.vehicle.vehicle_id, open_trunk)
        except (MazdaApiError, RuntimeError, TimeoutError) as err:
            raise HomeAssistantError(f"Mazda rejected the trunk command: {err}") from err
        await self.coordinator.async_refresh_until(
            lambda: self.is_locked is (not open_trunk),
        )
