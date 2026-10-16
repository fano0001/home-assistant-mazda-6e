import logging
import asyncio
import time

from datetime import timedelta
from collections.abc import Callable
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    DOMAIN,
    UPDATE_INTERVAL,
    CONF_INTERVAL_DEFAULT,
    CONF_INTERVAL_CHARGING,
    CONF_INTERVAL_PARKED,
    DEFAULT_INTERVAL_CHARGING,
    DEFAULT_INTERVAL_PARKED,
)
from .models import Mazda6eVehicle, ChargeStatus, VehicleStatus

CHARGING_STATES = {ChargeStatus.CHARGING.name, ChargeStatus.PAUSED.name}
PARKED_STATES = {VehicleStatus.PARKED.name}
PARKED_GRACE = 600

_LOGGER = logging.getLogger(__name__)


class Mazda6eCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, config_entry, mazda6e_api):
        """Initialize my coordinator."""
        opts = config_entry.options
        self._interval_default = opts.get(CONF_INTERVAL_DEFAULT, UPDATE_INTERVAL)
        self._interval_charging = opts.get(CONF_INTERVAL_CHARGING, DEFAULT_INTERVAL_CHARGING)
        self._interval_parked = opts.get(CONF_INTERVAL_PARKED, DEFAULT_INTERVAL_PARKED)
        self._last_active: dict[int, float] = {}

        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=self._interval_default),
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

        await self.async_refresh()
        return result

    async def async_refresh_until(
            self,
            state_matches: Callable[[], bool],
            *,
            attempts: int = 10,
            interval_seconds: float = 2.0,
    ) -> None:
        """Refresh until a control's state is visible in the vehicle status."""
        for attempt in range(attempts):
            await self.async_refresh()
            if state_matches():
                return
            if attempt < attempts - 1:
                await asyncio.sleep(interval_seconds)

    async def _async_get_function_config(self, vehicle_id: int) -> set[str]:
        """Fetch the vehicle's supported functions once and cache them."""
        if vehicle_id not in self._function_config:
            try:
                self._function_config[vehicle_id] = await self.api.async_get_function_config(vehicle_id)
            except Exception as err:
                _LOGGER.debug("Could not read function config for %s: %s", vehicle_id, err)
                self._function_config[vehicle_id] = set()

        return self._function_config[vehicle_id]

    # ------------------------------------------------------------------
    # Dynamic update interval
    # ------------------------------------------------------------------

    @staticmethod
    def _is_charging(status: dict | None) -> bool:
        raw = ((status or {}).get("charge") or {}).get("chargeStatus")
        return ChargeStatus.safe_name(raw) in CHARGING_STATES

    @staticmethod
    def _is_parked(status: dict | None) -> bool:
        raw = ((status or {}).get("vehicleStatus") or {}).get("status")
        return VehicleStatus.safe_name(raw) in PARKED_STATES

    def _select_interval(self, vehicle_status: dict) -> int:
        now = time.monotonic()

        seconds = []
        for vehicle_id, data in vehicle_status.items():
            status = data["status"]
            if self._is_charging(status):
                self._last_active[vehicle_id] = now
                seconds.append(self._interval_charging)
            elif self._is_parked(status):
                idle = now - self._last_active.setdefault(vehicle_id, now)
                seconds.append(
                    self._interval_default if idle < PARKED_GRACE else self._interval_parked
                )
            else:
                self._last_active[vehicle_id] = now
                seconds.append(self._interval_default)
        return min(seconds, default=self._interval_default)

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

        new_interval = timedelta(seconds=self._select_interval(vehicle_status))
        if new_interval != self.update_interval:
            _LOGGER.debug("Update interval changed to %s", new_interval)
            self.update_interval = new_interval

        _LOGGER.debug("vehicle_status: %s", vehicle_status)
        return vehicle_status
