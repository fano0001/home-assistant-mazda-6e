import logging
import asyncio
from contextlib import suppress
from time import monotonic

from datetime import timedelta
from collections.abc import Callable
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, UPDATE_INTERVAL, FALLBACK_UPDATE_INTERVAL
from .models import Mazda6eVehicle
from .api import MazdaApiError
from .mqtt import apply_status_patch, MqttStatusUpdates

_LOGGER = logging.getLogger(__name__)


class Mazda6eCoordinator(DataUpdateCoordinator):
    RECONNECT_INITIAL_DELAY = 5

    def __init__(self, hass, config_entry, mazda6e_api):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            # Push updates reset the coordinator's built-in polling timer.
            # Independent timers below guarantee a full refresh every 15 min.
            update_interval=None,
        )
        self.api = mazda6e_api
        self._function_config: dict[int, set[str]] = {}
        self._last_vehicle_status: dict[int, object] = {}
        self._control_locks: dict[int, asyncio.Lock] = {}
        self._mqtt_task = None
        self._catchup_task = None
        self._mqtt_ready = False
        self._stopping = False
        self._unsub_timers = []
        self._scheduled_refresh_tasks = set()
        self._refresh_lock = asyncio.Lock()
        self._full_refresh_updates = None
        self._mqtt_status_updates = {}
        self._mqtt_restart = asyncio.Event()
        self._vehicle_ids = set()
        self._full_refresh_failed = False
        self._mqtt_reports_received = 0
        self._last_mqtt_report = None

    @property
    def mqtt_diagnostics(self) -> dict:
        last = self._last_mqtt_report
        return {
            "connected": self._mqtt_ready,
            "reports_received": self._mqtt_reports_received,
            "seconds_since_report": None if last is None else round(monotonic() - last, 1),
            "full_refresh_interval_seconds": UPDATE_INTERVAL,
            "fallback_interval_seconds": FALLBACK_UPDATE_INTERVAL,
            "polling_enabled": not self.config_entry.pref_disable_polling,
            "fallback_active": not self.config_entry.pref_disable_polling and (
                not self._mqtt_ready or self._full_refresh_failed
            ),
        }

    def async_start(self) -> None:
        """Start push subscriptions and independent reconciliation timers."""
        self._mqtt_restart.clear()
        self._unsub_timers = [
            async_track_time_interval(
                self.hass, self._async_periodic_refresh, timedelta(seconds=UPDATE_INTERVAL),
            ),
            async_track_time_interval(
                self.hass, self._async_fallback_refresh, timedelta(seconds=FALLBACK_UPDATE_INTERVAL),
            ),
        ]
        self._mqtt_task = self.config_entry.async_create_background_task(
            self.hass, self._async_mqtt_loop(), "Mazda MQTT updates",
        )

    async def _async_periodic_refresh(self, _now) -> None:
        if not self._stopping and not self.config_entry.pref_disable_polling:
            await self._async_scheduled_refresh()

    async def _async_fallback_refresh(self, _now) -> None:
        if (not self._stopping and not self.config_entry.pref_disable_polling
                and (not self._mqtt_ready or self._full_refresh_failed)):
            await self._async_scheduled_refresh()

    async def _async_scheduled_refresh(self) -> None:
        task = asyncio.current_task()
        self._scheduled_refresh_tasks.add(task)
        try:
            await self.async_request_refresh()
        finally:
            self._scheduled_refresh_tasks.discard(task)

    def _handle_mqtt_update(self, vehicle_id, patch: dict) -> None:
        """Merge a partial MQTT state directly; no HTTP request per report."""
        if self._stopping or not self.data or vehicle_id not in self.data:
            return
        self._mqtt_reports_received += 1
        self._last_mqtt_report = monotonic()
        old = self.data[vehicle_id]
        current_status = old.get("status") or {}
        updated_status = apply_status_patch(current_status, patch)
        # Rejected stale reports must not become candidates for later refreshes.
        if updated_status != current_status:
            self._mqtt_status_updates.setdefault(vehicle_id, MqttStatusUpdates()).record(patch)
        if self._full_refresh_updates is not None:
            self._full_refresh_updates.setdefault(vehicle_id, []).append(patch)
        self.async_set_updated_data({
            **self.data,
            vehicle_id: {**old, "status": updated_status},
        })

    def _handle_mqtt_ready(self) -> None:
        self._mqtt_ready = True
        _LOGGER.info("Mazda MQTT notifications connected")
        # Reconcile changes missed while offline using the now-open session.
        if (not self.config_entry.pref_disable_polling
                and (self._catchup_task is None or self._catchup_task.done())):
            self._catchup_task = self.config_entry.async_create_task(
                self.hass, self.async_request_refresh(), "Mazda MQTT reconnect refresh",
            )

    async def _async_mqtt_loop(self) -> None:
        delay = self.RECONNECT_INITIAL_DELAY
        while not self._stopping:
            started = asyncio.get_running_loop().time()
            self._mqtt_restart.clear()
            listener = asyncio.create_task(self.api.async_listen_updates(
                tuple(self._vehicle_ids), self._handle_mqtt_update, self._handle_mqtt_ready,
            ))
            changed = asyncio.create_task(self._mqtt_restart.wait())
            try:
                done, _ = await asyncio.wait({listener, changed}, return_when=asyncio.FIRST_COMPLETED)
                if changed in done:
                    delay = self.RECONNECT_INITIAL_DELAY
                    continue
                await listener
            except asyncio.CancelledError:
                raise
            except ConfigEntryAuthFailed:
                # Let the coordinator's regular error handling start reauth.
                _LOGGER.warning("Mazda MQTT authentication failed; using polling")
            except Exception as err:
                # Do not expose broker payloads, credentials or vehicle IDs.
                reason = err.code if isinstance(err, MazdaApiError) and err.code else type(err).__name__
                _LOGGER.warning("Mazda MQTT unavailable (%s); using polling", reason)
            finally:
                self._mqtt_ready = False
                for task in (listener, changed):
                    task.cancel()
                for task in (listener, changed):
                    with suppress(asyncio.CancelledError, Exception):
                        await task
            if asyncio.get_running_loop().time() - started >= 60:
                delay = self.RECONNECT_INITIAL_DELAY
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)

    async def async_stop(self) -> None:
        """Remove timers and await all listener/transport cleanup on unload."""
        self._stopping = True
        for unsub in self._unsub_timers:
            unsub()
        self._unsub_timers.clear()
        scheduled = list(self._scheduled_refresh_tasks)
        for task in scheduled:
            task.cancel()
        for task in scheduled:
            with suppress(asyncio.CancelledError):
                await task
        if self._catchup_task is not None:
            self._catchup_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._catchup_task
            self._catchup_task = None
        if self._mqtt_task is not None:
            self._mqtt_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._mqtt_task
            self._mqtt_task = None

    async def async_shutdown(self) -> None:
        """Also stop custom timers/transports when HA shuts down the entry."""
        await self.async_stop()
        await super().async_shutdown()

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
        async with self._refresh_lock:
            self._full_refresh_updates = {}
            try:
                data = await self._async_fetch_full_data()
                # Reports received while HTTP/MQTT snapshots were in flight
                # must survive the older full response arriving afterwards.
                for vehicle_id, entry in data.items():
                    snapshot = entry["status"] or {}
                    current = snapshot
                    for patch in self._full_refresh_updates.get(vehicle_id, []):
                        current = apply_status_patch(current, patch)
                    updates = self._mqtt_status_updates.get(vehicle_id)
                    entry["status"] = updates.reconcile(snapshot, current=current) if updates else current
                self._mqtt_status_updates = {
                    vehicle_id: updates for vehicle_id, updates in self._mqtt_status_updates.items()
                    if vehicle_id in data
                }
                ids = set(data)
                if ids != self._vehicle_ids:
                    self._vehicle_ids = ids
                    self._mqtt_restart.set()
                self._full_refresh_failed = False
                return data
            except Exception:
                self._full_refresh_failed = True
                raise
            finally:
                self._full_refresh_updates = None

    async def _async_fetch_full_data(self):
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
            battery_preheating_plan = None
            if "#batteryScheduleHeating" in veh.functions:
                try:
                    battery_preheating_plan = await self.api.async_get_battery_preheating_plan(
                        veh.vehicle_id,
                    )
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

            vehicle_status[veh.vehicle_id] = {
                "vehicle": veh,
                "status": status_response,
                "battery_preheating_plan": battery_preheating_plan,
            }

        _LOGGER.debug("vehicle_status: %s", vehicle_status)
        return vehicle_status
