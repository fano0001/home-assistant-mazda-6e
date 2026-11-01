import logging
import asyncio

from datetime import timedelta
from collections.abc import Callable
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import CONF_BATTERY_CAPACITY, DOMAIN, EVENT_CHARGE, EVENT_TRIP, UPDATE_INTERVAL
from .helpers.charges import MAX_CHARGES, ChargeTracker
from .helpers.trips import DEFAULT_BATTERY_CAPACITY_KWH, MAX_TRIPS, TripTracker, estimate_short_trips
from .models import ChargeConnectionStatus, ChargeStatus, Mazda6eVehicle
from .api import MazdaApiError, battery_preheating_plan

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
        self._trip_store = Store(hass, 1, f"{DOMAIN}_trips_{config_entry.entry_id}")
        self._trackers: dict[str, TripTracker] = {}
        self._charge_trackers: dict[str, ChargeTracker] = {}
        self.trips: dict[str, list[dict]] = {}
        self.charges: dict[str, list[dict]] = {}

    async def async_load_trips(self) -> None:
        """Restore the trip and charge logs and anything still in progress."""
        stored = await self._trip_store.async_load() or {}
        self.trips = {
            vehicle_id: estimate_short_trips(trips, self._capacity()) for vehicle_id, trips in stored.get("trips", {}).items()
        }
        self._trackers = {
            vehicle_id: TripTracker(state) for vehicle_id, state in stored.get("trackers", {}).items()
        }
        self.charges = stored.get("charges", {})
        self._charge_trackers = {
            vehicle_id: ChargeTracker(state) for vehicle_id, state in stored.get("charge_trackers", {}).items()
        }

    def _trip_data(self) -> dict:
        return {
            "trips": self.trips,
            "trackers": {vehicle_id: tracker.as_dict() for vehicle_id, tracker in self._trackers.items()},
            "charges": self.charges,
            "charge_trackers": {
                vehicle_id: tracker.as_dict() for vehicle_id, tracker in self._charge_trackers.items()
            },
        }

    def _capacity(self) -> float:
        return float(self.config_entry.options.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY_KWH))

    def _track_trip(self, vehicle_id: int, status_response: dict | None) -> None:
        """Feed the poll to the trip tracker; a finished trip is logged and announced."""
        vehicle_status = (status_response or {}).get("vehicleStatus") or {}
        try:
            odometer = float(vehicle_status["totalMileage"])
        except (KeyError, TypeError, ValueError):
            odometer = None
        try:
            soc = float(vehicle_status["soc"])
        except (KeyError, TypeError, ValueError):
            soc = None

        key = str(vehicle_id)
        tracker = self._trackers.setdefault(key, TripTracker())
        before = tracker.save_key()
        trip = tracker.update(vehicle_status.get("status"), odometer, soc, dt_util.now(), self._capacity())

        if trip:
            self.trips[key] = estimate_short_trips([trip, *self.trips.get(key, [])][:MAX_TRIPS], self._capacity())
            trip = self.trips[key][0]
            self.hass.bus.async_fire(EVENT_TRIP, {"vehicle_id": vehicle_id, **trip})
            _LOGGER.info("Vehicle %s finished a %s km trip", vehicle_id, trip["distance_km"])
        if trip or tracker.save_key() != before:
            self._trip_store.async_delay_save(self._trip_data, 10)

    def _track_charge(self, vehicle_id: int, status_response: dict | None) -> None:
        """Feed the poll to the charge tracker; a finished session is logged and announced."""
        status_response = status_response or {}
        charge = status_response.get("charge") or {}
        ac = charge.get("chargeConStatus")
        dc = charge.get("dcChargeGunConnectStatus")
        connected = None if ac is None and dc is None else ChargeConnectionStatus.CONNECTED in (ac, dc)
        try:
            soc = float((status_response.get("vehicleStatus") or {})["soc"])
        except (KeyError, TypeError, ValueError):
            soc = None
        try:
            current = float(charge["chargeCurrent"])
        except (KeyError, TypeError, ValueError):
            current = None

        key = str(vehicle_id)
        tracker = self._charge_trackers.setdefault(key, ChargeTracker())
        session = tracker.update(
            connected,
            dc == ChargeConnectionStatus.CONNECTED,
            charge.get("chargeStatus") == ChargeStatus.CHARGING,
            soc,
            current,
            dt_util.now(),
            self._capacity(),
        )

        if session:
            self.charges[key] = [session, *self.charges.get(key, [])][:MAX_CHARGES]
            self.hass.bus.async_fire(EVENT_CHARGE, {"vehicle_id": vehicle_id, **session})
            _LOGGER.info("Vehicle %s finished a %s charge of %s kWh", vehicle_id, session["type"], session["energy_kwh"])
        # Shorter than the poll interval, or the rescheduled save would never run mid-session.
        if session or tracker.session:
            self._trip_store.async_delay_save(self._trip_data, 30)

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
            heating_plans = None
            if "#batteryScheduleHeating" in veh.functions:
                try:
                    heating_plans = await self.api.async_get_heating_plans(veh.vehicle_id)
                except MazdaApiError as err:
                    # Some vehicles advertise battery scheduling but their
                    # regional backend does not support this endpoint.
                    _LOGGER.debug(
                        "Could not read battery-preheating plan for %s: %s",
                        veh.vehicle_id,
                        err,
                    )

            status_code = (status_response or {}).get("vehicleStatus", {}).get("status")
            if self._last_vehicle_status.get(veh.vehicle_id) != status_code:
                _LOGGER.info("Vehicle %s status code changed to %s", veh.vehicle_id, status_code)
                self._last_vehicle_status[veh.vehicle_id] = status_code

            self._track_trip(veh.vehicle_id, status_response)
            self._track_charge(veh.vehicle_id, status_response)

            vehicle_status[veh.vehicle_id] = {
                "vehicle": veh,
                "status": status_response,
                "trips": self.trips.get(str(veh.vehicle_id), []),
                "charges": self.charges.get(str(veh.vehicle_id), []),
                "heating_plans": heating_plans,
                "battery_preheating_plan": battery_preheating_plan(heating_plans or []),
            }

        _LOGGER.debug("vehicle_status: %s", vehicle_status)
        return vehicle_status
