import logging
import asyncio

from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import DOMAIN, UPDATE_INTERVAL
from .models import Mazda6eVehicle

_LOGGER = logging.getLogger(__name__)


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
        self._function_config: dict[int, set[str]] = {}
        self._last_vehicle_status: dict[int, object] = {}
        self._control_locks: dict[int, asyncio.Lock] = {}

    def _get_control_lock(self, vehicle_id: int) -> asyncio.Lock:
        lock = self._control_locks.get(vehicle_id)
        if lock is None:
            lock = asyncio.Lock()
            self._control_locks[vehicle_id] = lock
        return lock

    async def async_execute_control_command(
        self,
        vehicle_id: int,
        function_code: str,
        *,
        params: dict | None = None,
        require_security_code: bool = False,
        max_attempts: int = 12,
        interval_seconds: float = 2.0,
        sign_omit_keys: set[str] | None = None,
    ) -> dict:
        """Execute one control command and refresh coordinator data afterward."""
        lock = self._get_control_lock(vehicle_id)

        async with lock:
            result = await self.api.async_submit_and_poll_control_command(
                vehicle_id,
                function_code,
                params=params,
                require_security_code=require_security_code,
                max_attempts=max_attempts,
                interval_seconds=interval_seconds,
                sign_omit_keys=sign_omit_keys,
            )

        await self.async_request_refresh()
        return result

    async def _async_get_function_config(self, vehicle_id: int) -> set[str]:
        """Fetch the vehicle's supported functions once and cache them."""
        if vehicle_id not in self._function_config:
            try:
                self._function_config[vehicle_id] = await self.api.async_get_function_config(vehicle_id)
            except Exception as err:
                _LOGGER.debug("Could not read function config for %s: %s", vehicle_id, err)
                self._function_config[vehicle_id] = set()

        return self._function_config[vehicle_id]

    async def _async_update_data(self):
        """Fetch data from API"""

        # get vehicles
        vehicles_response = await self.api.async_get_vehicles()

        _LOGGER.debug("vehicles_response: %s", vehicles_response)

        vehicles: list[Mazda6eVehicle] = vehicles_response
        vehicle_status = {}

        # get status for each vehicle
        for veh in vehicles:
            veh.functions = await self._async_get_function_config(veh.vehicle_id)
            status_response = await self.api.async_get_vehicle_status(veh.vehicle_id)

            status_code = (status_response or {}).get("vehicleStatus", {}).get("status")
            if self._last_vehicle_status.get(veh.vehicle_id) != status_code:
                _LOGGER.info("Vehicle %s status code changed to %s", veh.vehicle_id, status_code)
                self._last_vehicle_status[veh.vehicle_id] = status_code

            vehicle_status[veh.vehicle_id] = {
                "vehicle": veh,
                "status": status_response,
            }

        _LOGGER.debug("vehicle_status: %s", vehicle_status)
        return vehicle_status
