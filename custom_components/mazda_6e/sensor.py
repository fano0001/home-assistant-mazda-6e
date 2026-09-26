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
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import Mazda6eEntity
from .helpers.validators import remaining_charge_time, speed_value, temperature, timestamp_ms
from .models import Mazda6eVehicle, ChargeStatus, PowerStatus, SeatStatusMode, VehicleStatus

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class Mazda6eSensorDescription(SensorEntityDescription):
    """Description of a Mazda 6e Sensor."""
    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict], dict] | None = None
    skip_if_unavailable: bool = True


_SEAT_KEYS = {
    "front_left": "leftFront",
    "front_right": "rightFront",
    "rear_left": "leftBack",
    "rear_right": "rightBack",
}

# tires use the same left/right, front/back naming as the seats
_TIRE_KEYS = _SEAT_KEYS


def _seat(position: str) -> Mazda6eSensorDescription:
    return Mazda6eSensorDescription(
        key=f"seat_status_{position}",
        translation_key=f"seat_status_{position}",
        device_class=SensorDeviceClass.ENUM,
        options=[e.name for e in SeatStatusMode],
        value_fn=lambda data, p=_SEAT_KEYS[position]: SeatStatusMode.safe_name(
            data["status"]["seat"][p]["mode"]
        ),
        attrs_fn=lambda data, p=_SEAT_KEYS[position]: {
            "level": data["status"]["seat"][p].get("level"),
            "heat_status": data["status"]["seat"][p].get("heatStatus"),
            "vent_status": data["status"]["seat"][p].get("ventStatus"),
        },
    )


def _tire_pressure(position: str) -> Mazda6eSensorDescription:
    return Mazda6eSensorDescription(
        key=f"{position}_tire_pressure",
        translation_key=f"{position}_tire_pressure",
        icon="mdi:car-tire-alert",
        native_unit_of_measurement=UnitOfPressure.KPA,
        suggested_unit_of_measurement=UnitOfPressure.BAR,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data, p=_TIRE_KEYS[position]: data["status"]["tire"][p]["pressure"],
        attrs_fn=lambda data, p=_TIRE_KEYS[position]: {
            "status": data["status"]["tire"][p].get("status"),
        },
    )


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
        key="speed",
        translation_key="speed",
        icon="mdi:speedometer",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=speed_value,
    ),
    *(_tire_pressure(position) for position in _TIRE_KEYS),
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
        key="ac_charge_current",
        translation_key="ac_charge_current",
        icon="mdi:current-ac",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data["status"]["charge"]["acChargeCurrent"],
    ),
    Mazda6eSensorDescription(
        key="dc_charge_current",
        translation_key="dc_charge_current",
        icon="mdi:current-dc",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data["status"]["charge"]["dcChargeCurrent"],
    ),
    Mazda6eSensorDescription(
        key="charge_target_soc",
        translation_key="charge_target_soc",
        icon="mdi:battery-charging-high",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["charge"]["maxSocPercent"],
    ),
    Mazda6eSensorDescription(
        key="remainChargeTime",
        translation_key="remainChargeTime",
        icon="mdi:progress-clock",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        skip_if_unavailable=False,
        value_fn=lambda data: remaining_charge_time(data["status"]["charge"]["remainChargeTime"]),
    ),
    Mazda6eSensorDescription(
        key="chargeStatus",
        translation_key="chargeStatus",
        icon="mdi:state-machine",
        device_class=SensorDeviceClass.ENUM,
        options=[e.name for e in ChargeStatus],
        value_fn=lambda data: ChargeStatus.safe_name(data["status"]["charge"].get("chargeStatus"))
    ),
    *(_seat(position) for position in _SEAT_KEYS),
    Mazda6eSensorDescription(
        key="temperature_inside",
        translation_key="temperature_inside",
        icon="mdi:thermometer",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: temperature(data["status"]["hvac"]['insideTemp'])
    ),
    Mazda6eSensorDescription(
        key="temperature_cockpit",
        translation_key="temperature_cockpit",
        icon="mdi:thermometer-lines",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: temperature(data["status"]["hvac"]["consTempCockpit"]),
    ),
    Mazda6eSensorDescription(
        key="temperature_target",
        translation_key="temperature_target",
        icon="mdi:thermostat",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: temperature(data["status"]["hvac"]["remoteTemp"]),
    ),
    Mazda6eSensorDescription(
        key="humidity_inside",
        translation_key="humidity_inside",
        icon="mdi:water-percent",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["hvac"]['insideHumidity']
    ),
    Mazda6eSensorDescription(
        key="pm25_inside",
        translation_key="pm25_inside",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["hvac"]["insidePm25"],
    ),
    Mazda6eSensorDescription(
        key="power_status",
        translation_key="power_status",
        icon="mdi:power",
        device_class=SensorDeviceClass.ENUM,
        options=[e.name for e in PowerStatus],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: PowerStatus.safe_name(data["status"]["vehicleStatus"]["powerStatus"]),
    ),
    Mazda6eSensorDescription(
        key="vehicle_status",
        translation_key="vehicle_status",
        icon="mdi:car-info",
        device_class=SensorDeviceClass.ENUM,
        options=[e.name for e in VehicleStatus],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: VehicleStatus.safe_name(data["status"]["vehicleStatus"]["status"]),
    ),
    Mazda6eSensorDescription(
        key="vehicle_status_code",
        translation_key="vehicle_status_code",
        icon="mdi:code-tags",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data["status"]["vehicleStatus"]["status"],
    ),
    Mazda6eSensorDescription(
        key="last_updated",
        translation_key="last_updated",
        icon="mdi:clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: timestamp_ms(data["status"]["lastUpdatedAt"]),
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
                value = description.value_fn(data)
            except Exception:
                continue
            if value is None and description.skip_if_unavailable:
                continue

            entities.append(
                Mazda6eSensor(
                    coordinator=coordinator,
                    vehicle=vehicle,
                    description=description,
                )
            )

    async_add_entities(entities)


class Mazda6eSensor(Mazda6eEntity, SensorEntity):
    """Mazda 6e base sensor."""

    entity_description: Mazda6eSensorDescription

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
        attributes = self.vehicle_attributes

        if not self.entity_description.attrs_fn:
            return attributes

        try:
            attributes.update(self.entity_description.attrs_fn(self.vehicle_data))
        except Exception as err:
            _LOGGER.debug(
                "Failed to compute attributes for %s: %s",
                self.entity_id,
                err,
            )
        return attributes
