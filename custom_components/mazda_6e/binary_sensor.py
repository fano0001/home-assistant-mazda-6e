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
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import Mazda6eEntity
from .models import Mazda6eVehicle, ChargeConnectionStatus, ChargeStatus, LOCK_UNLOCKED

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Mazda6eBinarySensorDescription(BinarySensorEntityDescription):
    """Description of a Mazda 6e binary sensors."""
    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict], dict] | None = None


_OPENING_POSITIONS = ("front_left", "front_right", "rear_left", "rear_right")


def _door(index: int, position: str) -> Mazda6eBinarySensorDescription:
    return Mazda6eBinarySensorDescription(
        key=f"{position}_door",
        translation_key=f"{position}_door",
        icon="mdi:car-door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda data, i=index: data["status"]["door"]["doors"][i],
    )


def _window(index: int, position: str) -> Mazda6eBinarySensorDescription:
    return Mazda6eBinarySensorDescription(
        key=f"{position}_window",
        translation_key=f"{position}_window",
        icon="mdi:window-closed-variant",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda data, i=index: data["status"]["window"]["windows"][i],
        attrs_fn=lambda data, i=index: {
            "open_degree": data["status"]["window"]["openDegree"][i],
        },
    )


def _lamp(key: str, api_key: str) -> Mazda6eBinarySensorDescription:
    return Mazda6eBinarySensorDescription(
        key=key,
        translation_key=key,
        device_class=BinarySensorDeviceClass.LIGHT,
        entity_registry_enabled_default=False,
        value_fn=lambda data, k=api_key: bool(data["status"]["lamp"][k]),
    )


SENSOR_TYPES: tuple[Mazda6eBinarySensorDescription, ...] = (
    *(_door(i, position) for i, position in enumerate(_OPENING_POSITIONS)),
    Mazda6eBinarySensorDescription(
        key="trunk",
        translation_key="trunk",
        icon="mdi:car-back",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda data: data["status"]["door"]["trunk"],
    ),
    *(_window(i, position) for i, position in enumerate(_OPENING_POSITIONS)),
    Mazda6eBinarySensorDescription(
        key="driver_lock",
        translation_key="driver_lock",
        device_class=BinarySensorDeviceClass.LOCK,
        value_fn=lambda data: data["status"]["door"]["driverLock"] == LOCK_UNLOCKED,
    ),
    Mazda6eBinarySensorDescription(
        key="passenger_lock",
        translation_key="passenger_lock",
        device_class=BinarySensorDeviceClass.LOCK,
        value_fn=lambda data: data["status"]["door"]["passengerLock"] == LOCK_UNLOCKED,
    ),
    _lamp("low_beam", "lowBeam"),
    _lamp("high_beam", "highBeam"),
    _lamp("position_lamp", "positionLamp"),
    _lamp("left_turn_signal", "leftTurn"),
    _lamp("right_turn_signal", "rightTurn"),
    Mazda6eBinarySensorDescription(
        key="plugged_in",
        translation_key="plugged_in",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda data: data["status"]["charge"]["chargeConStatus"] == ChargeConnectionStatus.CONNECTED,
    ),
    Mazda6eBinarySensorDescription(
        key="dc_plugged_in",
        translation_key="dc_plugged_in",
        device_class=BinarySensorDeviceClass.PLUG,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data["status"]["charge"]["dcChargeGunConnectStatus"] == ChargeConnectionStatus.CONNECTED,
    ),
    Mazda6eBinarySensorDescription(
        key="is_charging",
        translation_key="is_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: data["status"]["charge"]["chargeStatus"] == ChargeStatus.CHARGING,
    ),
    Mazda6eBinarySensorDescription(
        key="air_conditioning",
        translation_key="air_conditioning",
        icon="mdi:air-conditioner",
        value_fn=lambda data: bool(data["status"]["hvac"]["acStatus"]),
    ),
    Mazda6eBinarySensorDescription(
        key="defrost",
        translation_key="defrost",
        icon="mdi:car-defrost-front",
        value_fn=lambda data: bool(data["status"]["hvac"]["defrostStatus"]),
    ),
    Mazda6eBinarySensorDescription(
        key="steering_wheel_heater",
        translation_key="steering_wheel_heater",
        icon="mdi:steering",
        value_fn=lambda data: bool(data["status"]["vehicleStatus"]["steeringWheelHeater"]),
    ),
    Mazda6eBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: bool(data["status"]["vehicleStatus"]["connectStatus"]),
    ),
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
                    description=description,
                )
            )

    async_add_entities(entities)


class Mazda6eBinarySensor(Mazda6eEntity, BinarySensorEntity):
    entity_description: Mazda6eBinarySensorDescription

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

    @property
    def extra_state_attributes(self) -> dict:
        attributes = self.vehicle_attributes

        if not self.entity_description.attrs_fn:
            return attributes

        try:
            attributes.update(self.entity_description.attrs_fn(self.vehicle_data))
        except Exception as err:
            _LOGGER.debug("Failed to compute attributes for %s: %s", self.entity_id, err)
        return attributes
