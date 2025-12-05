from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers import aiohttp_client

from .api import Mazda6EApi
from .const import DOMAIN, UPDATE_INTERVAL
from .models import Mazda6eVehicle



class Mazda6eCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        self.api = Mazda6EApi(
            aiohttp_client.async_get_clientsession(hass),
            entry.data["email_enc"],
            entry.data["password_enc"],
        )

        super().__init__(
            hass,
            logger=__import__("logging").getLogger(DOMAIN),
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    async def _async_update_data(self):
        vehicles: list[Mazda6eVehicle] = await self.api.async_get_vehicles()
        vehicle_status = {}

        for veh in vehicles:
            status = await self.api.async_get_vehicle_status(veh.vehicle_id)
            vehicle_status[veh.vehicle_id] = {
                "vehicle": veh,
                "status": status,
            }

        return vehicle_status
