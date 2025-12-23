from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength, PERCENTAGE, UnitOfPressure, UnitOfSpeed, UnitOfElectricCurrent, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN
from .models import Mazda6eVehicle, ChargeStatus, SeatStatusMode

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Mazda6eSensorDescription(SensorEntityDescription):
    """Description of a Mazda 6e Sensor."""
    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict], dict] | None = None


SENSOR_TYPES: tuple[Mazda6eSensorDescription, ...] = (
    Mazda6eSensorDescription(
        key="battery_state_of_charge",
        translation_key="battery_state_of_charge",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["vehicleStatus"]["soc"],
    ),
    Mazda6eSensorDescription(
        key="remaining_driving_range",
        translation_key="remaining_driving_range",
        icon="mdi:ev-station",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["vehicleStatus"]["drvMileage"],
    ),
    Mazda6eSensorDescription(
        key="odometer",
        translation_key="odometer",
        icon="mdi:speedometer",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: data["status"]["vehicleStatus"]["totalMileage"],
    ),
    Mazda6eSensorDescription(
        key="current_speed",
        translation_key="current_speed",
        icon="mdi:speedometer",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["vehicleStatus"]["speed"],
    ),
    Mazda6eSensorDescription(
        key="front_left_tire_pressure",
        translation_key="front_left_tire_pressure",
        icon="mdi:car-tire-alert",
        native_unit_of_measurement=UnitOfPressure.KPA,
        device_class=SensorDeviceClass.PRESSURE,
        suggested_unit_of_measurement=UnitOfPressure.BAR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["tire"]["leftFront"]["pressure"],
    ),
    Mazda6eSensorDescription(
        key="front_right_tire_pressure",
        translation_key="front_right_tire_pressure",
        icon="mdi:car-tire-alert",
        native_unit_of_measurement=UnitOfPressure.KPA,
        suggested_unit_of_measurement=UnitOfPressure.BAR,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["tire"]["rightFront"]["pressure"],
    ),
    Mazda6eSensorDescription(
        key="rear_left_tire_pressure",
        translation_key="rear_left_tire_pressure",
        icon="mdi:car-tire-alert",
        native_unit_of_measurement=UnitOfPressure.KPA,
        suggested_unit_of_measurement=UnitOfPressure.BAR,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["tire"]["leftBack"]["pressure"],
    ),
    Mazda6eSensorDescription(
        key="rear_right_tire_pressure",
        translation_key="rear_right_tire_pressure",
        icon="mdi:car-tire-alert",
        native_unit_of_measurement=UnitOfPressure.KPA,
        suggested_unit_of_measurement=UnitOfPressure.BAR,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["tire"]["rightBack"]["pressure"],
    ),
    Mazda6eSensorDescription(
        key="chargeCurrent",
        translation_key="chargeCurrent",
        icon="mdi:current-ac",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["charge"]["chargeCurrent"],
    ),
    Mazda6eSensorDescription(
        key="remainChargeTime",
        translation_key="remainChargeTime",
        icon="mdi:progress-clock",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["charge"]["remainChargeTime"],
    ),
    Mazda6eSensorDescription(
        key="chargeStatus",
        translation_key="chargeStatus",
        icon="mdi:state-machine",
        device_class=SensorDeviceClass.ENUM,
        options=[e.name for e in ChargeStatus],
        value_fn=lambda data: ChargeStatus.safe_name(data["status"]["charge"].get("chargeStatus"))
    ),
    Mazda6eSensorDescription(
        key="seat_status_front_left",
        translation_key="seat_status_front_left",
        device_class=SensorDeviceClass.ENUM,
        options=[e.name for e in SeatStatusMode],
        value_fn=lambda data: SeatStatusMode.safe_name(data["status"]["seat"]['leftFront']['mode']),
        attrs_fn=lambda data: {
            "level": data["status"]["seat"]["leftFront"]["level"],
            "heat_status": data["status"]["seat"]["leftFront"]["heatStatus"],
            "vent_status": data["status"]["seat"]["leftFront"]["ventStatus"]
        }
    ),
    Mazda6eSensorDescription(
        key="seat_status_front_right",
        translation_key="seat_status_front_right",
        device_class=SensorDeviceClass.ENUM,
        options=[e.name for e in SeatStatusMode],
        value_fn=lambda data: SeatStatusMode.safe_name(data["status"]["seat"]['rightFront']['mode']),
        attrs_fn=lambda data: {
            "level": data["status"]["seat"]["rightFront"]["level"],
            "heat_status": data["status"]["seat"]["rightFront"]["heatStatus"],
            "vent_status": data["status"]["seat"]["rightFront"]["ventStatus"]
        }
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
                Mazda6eSensor(
                    coordinator=coordinator,
                    vehicle=vehicle,
                    vehicle_id=f"{vehicle.vehicle_id}",
                    description=description,
                )
            )

    async_add_entities(entities)


class Mazda6eSensor(CoordinatorEntity, SensorEntity):
    """Mazda 6e base sensor."""

    _attr_has_entity_name = True
    entity_description: Mazda6eSensorDescription

    def __init__(
            self,
            coordinator,
            vehicle: Mazda6eVehicle,
            vehicle_id: str,
            description: Mazda6eSensorDescription,
    ):
        super().__init__(coordinator)
        self.entity_description = description
        self.vehicle = vehicle
        self.vehicle_id = vehicle_id

        _LOGGER.debug("Mazda6eSensor: '%s', '%s'", self.entity_description, self.vehicle)

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
    def native_value(self):
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
        """Return extra attributes for the sensor."""
        if not self.entity_description.attrs_fn:
            return {}

        try:
            data = self.coordinator.data[self.vehicle.vehicle_id]
            return self.entity_description.attrs_fn(data)
        except Exception as err:
            _LOGGER.debug(
                "Failed to compute attributes for %s: %s",
                self.entity_id,
                err,
            )
            return {}
