"""Services that manage Mazda 6e charging and battery-preheating plans."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util

from .api import MazdaApiError
from .const import DOMAIN
from .helpers.charge_plan import (
    charge_plans,
    first_charge_plan,
    format_plan_time,
    plan_time_zone,
    upcoming_end_data,
)

ATTR_DEVICE_ID = "device_id"
ATTR_START_TIME = "start_time"
ATTR_END_TIME = "end_time"
ATTR_DEPARTURE_TIME = "departure_time"

SERVICE_CREATE_CHARGE_SCHEDULE = "create_charge_schedule"
SERVICE_DELETE_CHARGE_SCHEDULE = "delete_charge_schedule"
SERVICE_CREATE_BATTERY_PREHEATING = "create_battery_preheating"

COMMAND_ERRORS = (KeyError, MazdaApiError, RuntimeError, TimeoutError, ValueError)

DEVICE_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})
CREATE_CHARGE_SCHEDULE_SCHEMA = DEVICE_SCHEMA.extend({
    vol.Required(ATTR_START_TIME): cv.time,
    vol.Optional(ATTR_END_TIME): cv.time,
})
CREATE_BATTERY_PREHEATING_SCHEMA = DEVICE_SCHEMA.extend({
    vol.Required(ATTR_DEPARTURE_TIME): cv.time,
})


def _resolve_vehicle(hass: HomeAssistant, device_id: str):
    """Return the coordinator and vehicle id behind a Mazda 6e device."""
    device = dr.async_get(hass).async_get(device_id)
    vehicle_ids = [identifier for domain, identifier in (device.identifiers if device else ()) if domain == DOMAIN]
    if not vehicle_ids:
        raise ServiceValidationError("Select a Mazda 6e vehicle")
    for entry_id in device.config_entries:
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            continue
        for vehicle_id, item in coordinator.data.items():
            if str(vehicle_id) == vehicle_ids[0]:
                return coordinator, vehicle_id, item
    raise ServiceValidationError("The Mazda 6e vehicle is not loaded")


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the plan services."""

    async def create_charge_schedule(call: ServiceCall) -> None:
        coordinator, vehicle_id, item = _resolve_vehicle(hass, call.data[ATTR_DEVICE_ID])
        if charge_plans(item) is None:
            raise ServiceValidationError("This vehicle does not report charging schedules")
        if first_charge_plan(item) is not None:
            raise ServiceValidationError("A charging schedule already exists; edit or delete it instead")
        end = call.data.get(ATTR_END_TIME)
        try:
            await coordinator.api.async_add_charge_plan(
                vehicle_id,
                format_plan_time(call.data[ATTR_START_TIME]),
                format_plan_time(end) if end else "0000",
                end_enabled=end is not None,
                time_zone=plan_time_zone(dt_util.now()),
            )
        except COMMAND_ERRORS as err:
            raise HomeAssistantError(f"Mazda rejected the create charging schedule command: {err}") from err
        await coordinator.async_refresh_until(
            lambda: first_charge_plan(coordinator.data.get(vehicle_id)) is not None,
        )

    async def delete_charge_schedule(call: ServiceCall) -> None:
        coordinator, vehicle_id, item = _resolve_vehicle(hass, call.data[ATTR_DEVICE_ID])
        plan = first_charge_plan(item)
        if plan is None:
            raise ServiceValidationError("There is no charging schedule to delete")
        try:
            await coordinator.api.async_delete_charge_plan(vehicle_id, plan["planId"])
        except COMMAND_ERRORS as err:
            raise HomeAssistantError(f"Mazda rejected the delete charging schedule command: {err}") from err
        await coordinator.async_refresh_until(
            lambda: all(
                p.get("planId") != plan["planId"]
                for p in charge_plans(coordinator.data.get(vehicle_id)) or []
            ),
        )

    async def create_battery_preheating(call: ServiceCall) -> None:
        coordinator, vehicle_id, item = _resolve_vehicle(hass, call.data[ATTR_DEVICE_ID])
        if item.get("heating_plans") is None:
            raise ServiceValidationError("This vehicle does not report battery-preheating plans")
        if item.get("battery_preheating_plan") is not None:
            raise ServiceValidationError("A battery-preheating plan already exists; edit or delete it instead")
        end_data = upcoming_end_data(dt_util.now(), call.data[ATTR_DEPARTURE_TIME])
        try:
            await coordinator.api.async_add_battery_preheating(vehicle_id, end_data)
        except COMMAND_ERRORS as err:
            raise HomeAssistantError(f"Mazda rejected the create battery preheating command: {err}") from err
        await coordinator.async_refresh_until(
            lambda: coordinator.data.get(vehicle_id, {}).get("battery_preheating_plan") is not None,
        )

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_CHARGE_SCHEDULE, create_charge_schedule, schema=CREATE_CHARGE_SCHEDULE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_CHARGE_SCHEDULE, delete_charge_schedule, schema=DEVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_BATTERY_PREHEATING, create_battery_preheating,
        schema=CREATE_BATTERY_PREHEATING_SCHEMA,
    )
