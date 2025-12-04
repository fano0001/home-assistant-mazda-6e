from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        MazdaBatterySensor(coordinator),
        MazdaRangeSensor(coordinator),
    ])


class MazdaBatterySensor(CoordinatorEntity, SensorEntity):
    _attr_name = "Mazda 6e Batterie"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = "mazda6e_battery"

    @property
    def native_value(self):
        return self.coordinator.data["battery"]["level"]


class MazdaRangeSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "Mazda 6e Reichweite"
    _attr_native_unit_of_measurement = "km"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = "mazda6e_range"

    @property
    def native_value(self):
        return self.coordinator.data["battery"]["range_km"]
