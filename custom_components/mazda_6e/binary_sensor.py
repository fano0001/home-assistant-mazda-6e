from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN
from .models import Mazda6eVehicle, ChargeConnectionStatus, ChargeStatus

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Mazda6eBinarySensorDescription(BinarySensorEntityDescription):
    """Description of a Mazda 6e binary sensors."""
    value_fn: Callable[[dict[str, Any]], Any]


SENSOR_TYPES: tuple[Mazda6eBinarySensorDescription, ...] = (
    Mazda6eBinarySensorDescription(
        key="front_left_door",
        translation_key="front_left_door",
        icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda data: data["status"]["door"]["doors"][0],
    ),
    Mazda6eBinarySensorDescription(
        key="front_right_door",
        translation_key="front_right_door",
        icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda data: data["status"]["door"]["doors"][1],
    ),
    Mazda6eBinarySensorDescription(
        key="rear_left_door",
        translation_key="rear_left_door",
        icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda data: data["status"]["door"]["doors"][2],
    ),
    Mazda6eBinarySensorDescription(
        key="rear_right_door",
        translation_key="rear_right_door",
        icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda data: data["status"]["door"]["doors"][3],
    ),
    Mazda6eBinarySensorDescription(
        key="trunk",
        translation_key="trunk",
        icon="mdi:car-back",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda data: data["status"]["door"]["trunk"],
    ),
    Mazda6eBinarySensorDescription(
        key="front_left_window",
        translation_key="front_left_window",
        icon="mdi:window-closed-variant",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda data: data["status"]["window"]["windows"][0],
    ),
    Mazda6eBinarySensorDescription(
        key="front_right_window",
        translation_key="front_right_window",
        icon="mdi:window-closed-variant",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda data: data["status"]["window"]["windows"][1],
    ),
    Mazda6eBinarySensorDescription(
        key="rear_left_window",
        translation_key="rear_left_window",
        icon="mdi:window-closed-variant",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda data: data["status"]["window"]["windows"][2],
    ),
    Mazda6eBinarySensorDescription(
        key="rear_right_window",
        translation_key="rear_right_window",
        icon="mdi:window-closed-variant",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda data: data["status"]["window"]["windows"][3],
    ),
    Mazda6eBinarySensorDescription(
        key="sunroof",
        translation_key="sunroof",
        icon="mdi:blinds-vertical",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda data: data["status"]["window"]["sunroof"],
    ),
    Mazda6eBinarySensorDescription(
        key="plugged_in",
        translation_key="plugged_in",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda data: data["status"]["charge"]["chargeConStatus"] == ChargeConnectionStatus.CONNECTED,
    ),
    Mazda6eBinarySensorDescription(
        key="is_charging",
        translation_key="is_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: data["status"]["charge"]["chargeStatus"] == ChargeStatus.CHARGING,
    )
)


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []

    for vehicle_id, data in coordinator.data.items():
        vehicle: Mazda6eVehicle = data["vehicle"]

        for description in SENSOR_TYPES:
            try:
                description.value_fn(data)
            except Exception:
                continue

            entities.append(
                Mazda6eBinarySensor(
                    coordinator=coordinator,
                    vehicle=vehicle,
                    vehicle_id=f"{vehicle.vehicle_id}",
                    description=description,
                )
            )

    async_add_entities(entities)


class Mazda6eBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    entity_description: Mazda6eBinarySensorDescription

    def __init__(
            self,
            coordinator,
            vehicle: Mazda6eVehicle,
            vehicle_id: str,
            description: Mazda6eBinarySensorDescription,
    ):
        super().__init__(coordinator)
        self.entity_description = description
        self.vehicle = vehicle
        self.vehicle_id = vehicle_id

        self._attr_unique_id = f"{vehicle.vehicle_id}_{description.key}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vehicle_id)},
            name=f"Mazda 6e - {vehicle.vehicle_id}",
            serial_number=vehicle.vin,
            manufacturer="Mazda",
            model="6e",
        )

    @property
    def vehicle_data(self) -> dict | None:
        return self.coordinator.data.get(self.vehicle.vehicle_id)

    @property
    def is_on(self):
        data = self.vehicle_data
        if not data:
            return None

        try:
            return self.entity_description.value_fn(data)
        except Exception as err:
            _LOGGER.warning(
                "Mazda6eSensor '%s' could not read value: %s",
                self.entity_description.key,
                err,
            )
            return None
