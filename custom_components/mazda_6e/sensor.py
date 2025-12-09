from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .models import Mazda6eVehicle

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}")


# ================================================================
#  SENSOR DESCRIPTIONS
# ================================================================

@dataclass(frozen=True, kw_only=True)
class Mazda6eSensorDescription(SensorEntityDescription):
    """Beschreibung eines Mazda 6e Sensors."""
    value_fn: Callable[[dict[str, Any]], Any]


SENSOR_TYPES: tuple[Mazda6eSensorDescription, ...] = (
    Mazda6eSensorDescription(
        key="soc",
        translation_key="battery_state_of_charge",
        icon="mdi:battery",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda data: data["status"]["vehicleStatus"]["soc"],
    ),
    Mazda6eSensorDescription(
        key="range",
        translation_key="remaining_driving_range",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_fn=lambda data: data["status"]["vehicleStatus"]["drvMileage"],
    ),
)


# ================================================================
#  SETUP ENTRY
# ================================================================

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
                    vehicle_id=vehicle_id,
                    description=description,
                )
            )

    async_add_entities(entities)


# ================================================================
#  BASE ENTITY
# ================================================================

class Mazda6eSensor(CoordinatorEntity, SensorEntity):
    """Ein generischer Mazda 6e Sensor."""

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

        # Modellname fallback
        model = vehicle.model_name or "Mazda 6e"
        model_slug = model.lower().replace(" ", "_")

        human_name = (
            description.translation_key.replace("_", " ").capitalize()
            if description.translation_key
            else description.key.replace("_", " ").capitalize()
        )

        # unique_id: mazda6e_<models>_<id>_<sensor>
        self._attr_unique_id = f"mazda6e_{model_slug}_{vehicle_id}_{description.key}"

        # finaler Anzeigename
        self._attr_name = f"{model} {human_name}"

        # Icon & Einheit übernehmen
        self._attr_icon = description.icon
        self._attr_native_unit_of_measurement = (
            description.native_unit_of_measurement
        )

    @property
    def vehicle_data(self) -> dict | None:
        return self.coordinator.data.get(self.vehicle_id)

    @property
    def native_value(self):
        data = self.vehicle_data
        if not data:
            return None

        try:
            return self.entity_description.value_fn(data)
        except Exception as err:
            _LOGGER.warning(
                "Mazda6eSensor '%s' konnte Wert nicht lesen: %s",
                self.entity_description.key,
                err,
            )
            return None
