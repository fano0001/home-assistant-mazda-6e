import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfLength

from .const import DOMAIN

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}")


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    for vehicle_id, data in coordinator.data.items():
        vehicle = data["vehicle"]

        entities.append(Mazda6eSocSensor(coordinator, vehicle_id, vehicle))
        entities.append(Mazda6eRangeSensor(coordinator, vehicle_id, vehicle))

    async_add_entities(entities)


class Mazda6eBaseEntity(CoordinatorEntity, SensorEntity):
    """Gemeinsame Basis für alle Mazda Sensoren."""

    def __init__(self, coordinator, vehicle_id, vehicle):
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id
        self.vehicle = vehicle

        # 🔥 Einheitliche Basis für IDs
        model_slug = self.vehicle.model_name.lower().replace(" ", "_")  # "mazda_6e"
        entity_type = self.__class__.__name__.replace("Mazda6e", "").replace("Sensor", "").lower()

        # 🔥 Unique ID (wichtig für HA)
        # sensor.<domain>_<model>_<vehicleid>_<type>
        self._attr_unique_id = f"{model_slug}_{vehicle_id}_{entity_type}"

        # 🔥 Benutzerfreundlicher Name
        # z.B.: "Mazda 6e Batterie"
        human_name = entity_type.capitalize().replace("soc", "Batterie").replace("range", "Reichweite")
        self._attr_name = f"{self.vehicle.model_name} {human_name}"

    @property
    def vehicle_data(self):
        """Bequemer Zugriff auf die Daten dieses Fahrzeugs."""
        return self.coordinator.data.get(self.vehicle_id)


class Mazda6eSocSensor(Mazda6eBaseEntity):
    """State of Charge"""

    _attr_icon = "mdi:battery"
    _attr_native_unit_of_measurement = "%"

    @property
    def native_value(self):
        data = self.vehicle_data
        if not data:
            return None
        return data["status"]['vehicleStatus']['soc']


class Mazda6eRangeSensor(Mazda6eBaseEntity):
    """Reichweite"""

    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS

    @property
    def native_value(self):
        data = self.vehicle_data
        if not data:
            return None
        return data["status"]['vehicleStatus']['drvMileage']
