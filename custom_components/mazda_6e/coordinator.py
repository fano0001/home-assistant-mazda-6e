from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers import aiohttp_client

from .api import Mazda6EApi
from .const import DOMAIN, UPDATE_INTERVAL
from .models import Mazda6eVehicle


class Mazda6eCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, config_entry, mazda6eApi):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(DOMAIN),
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.api = mazda6eApi

    async def _async_update_data(self):
        vehicles: list[Mazda6eVehicle] = await self.api.async_get_vehicles(self.api.deviceid)
        vehicle_status = {}

        for veh in vehicles:
            status = await self.api.async_get_vehicle_status(veh.vehicle_id, self.api.deviceid)
            vehicle_status[veh.vehicle_id] = {
                "vehicle": veh,
                "status": status,
            }

        return vehicle_status
