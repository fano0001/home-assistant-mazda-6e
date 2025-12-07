from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import UnitOfEnergy, UnitOfLength

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    # Der Coordinator liefert ein dict: { vehicle_id: { "vehicle": Mazda6eVehicle, "status": {...} } }
    for vehicle_id, data in coordinator.data.items():
        vehicle = data["vehicle"]

        entities.append(Mazda6eSocSensor(coordinator, vehicle_id, vehicle))
        entities.append(Mazda6eRangeSensor(coordinator, vehicle_id, vehicle))

    async_add_entities(entities)


class Mazda6eBaseEntity(CoordinatorEntity, SensorEntity):
    """Gemeinsame Basis für alle Sensoren"""

    def __init__(self, coordinator, vehicle_id, vehicle):
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id
        self.vehicle = vehicle

        # Eindeutige ID pro Entity & Fahrzeug
        self._attr_unique_id = f"{vehicle_id}_{self.__class__.__name__}"

        # Benutzerfreundlicher Name
        self._attr_name = f"{vehicle.model_name} {self.__class__.__name__}"


class Mazda6eSocSensor(Mazda6eBaseEntity):
    """SOC-Sensor"""

    _attr_icon = "mdi:battery"
    _attr_native_unit_of_measurement = "%"

    @property
    def native_value(self):
        data = self.coordinator.data.get(self.vehicle_id)
        if not data:
            return None
        return data["status"].get("soc")


class Mazda6eRangeSensor(Mazda6eBaseEntity):
    """Reichweiten-Sensor"""

    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS

    @property
    def native_value(self):
        data = self.coordinator.data.get(self.vehicle_id)
        if not data:
            return None
        return data["status"].get("drvMileage")
