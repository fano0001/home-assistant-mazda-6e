from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        Mazda6eSocSensor(coordinator),
        Mazda6eRangeSensor(coordinator),
    ])


class Mazda6eSocSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:battery"

    @property
    def native_value(self):
        return self.coordinator.data[self.vehicle_id]["vehicleStatus"]["soc"]


class Mazda6eRangeSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:map-marker-distance"

    @property
    def native_value(self):
        return self.coordinator.data[self.vehicle_id]["vehicleStatus"]["drvMileage"]
