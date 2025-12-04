from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import Mazda6eApi
from .const import DOMAIN, UPDATE_INTERVAL


class Mazda6eCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        self.api = Mazda6eApi(
            hass.helpers.aiohttp_client.async_get_clientsession(hass),
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
        return await self.api.get_vehicle_status()
