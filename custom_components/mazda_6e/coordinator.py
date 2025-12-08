import logging

from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import DOMAIN, UPDATE_INTERVAL
from .models import Mazda6eVehicle

_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}")

class Mazda6eCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, config_entry, mazda6e_api):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.api = mazda6e_api

    async def _async_update_data(self):
        """Fetch data from API"""
        # -------------------------
        # 1) Fahrzeuge abrufen
        # -------------------------
        vehicles_response = await self.api.async_get_vehicles(self.api.deviceid)

        _LOGGER.debug("vehicles_response: %s", vehicles_response)

        vehicles: list[Mazda6eVehicle] = vehicles_response
        vehicle_status = {}

        # -------------------------
        # 2) Status pro Fahrzeug
        # -------------------------
        for veh in vehicles:
            status_response = await self.api.async_get_vehicle_status(
                veh.vehicle_id, self.api.deviceid
            )

            vehicle_status[veh.vehicle_id] = {
                "vehicle": veh,
                "status": status_response,
            }

        _LOGGER.debug("vehicle_status: %s", vehicle_status)
        return vehicle_status
