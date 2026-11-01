"""Battery-preheating and charge-schedule times for Mazda 6e vehicles."""

from datetime import datetime, time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import MazdaApiError
from .const import DOMAIN
from .entity import Mazda6eEntity
from .helpers.charge_plan import (
    END_DATA_FORMAT,
    charge_plans,
    first_charge_plan,
    format_plan_time,
    parse_plan_time,
    plan_end_enabled,
    plan_time_zone,
    upcoming_end_data,
)

BATTERY_PREHEATING_DEPARTURE_TIME_DESCRIPTION = TimeEntityDescription(
    key="battery_preheating_departure_time",
    translation_key="battery_preheating_departure_time",
    icon="mdi:clock-outline",
    entity_category=EntityCategory.CONFIG,
)

CHARGE_SCHEDULE_START_DESCRIPTION = TimeEntityDescription(
    key="charge_schedule_start",
    translation_key="charge_schedule_start",
    icon="mdi:clock-start",
    entity_category=EntityCategory.CONFIG,
)

CHARGE_SCHEDULE_END_DESCRIPTION = TimeEntityDescription(
    key="charge_schedule_end",
    translation_key="charge_schedule_end",
    icon="mdi:clock-end",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up battery-preheating and charge-schedule time controls."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for item in coordinator.data.values():
        vehicle = item["vehicle"]
        if item.get("heating_plans") is not None:
            entities.append(Mazda6eBatteryPreheatingDepartureTime(coordinator, vehicle))
        if charge_plans(item) is not None:
            entities.append(Mazda6eChargeScheduleTime(coordinator, vehicle, CHARGE_SCHEDULE_START_DESCRIPTION))
            entities.append(Mazda6eChargeScheduleTime(coordinator, vehicle, CHARGE_SCHEDULE_END_DESCRIPTION))
    async_add_entities(entities)


class Mazda6eBatteryPreheatingDepartureTime(Mazda6eEntity, TimeEntity):
    """Represent the departure time of the battery-preheating plan."""

    def __init__(self, coordinator, vehicle) -> None:
        super().__init__(coordinator, vehicle, BATTERY_PREHEATING_DEPARTURE_TIME_DESCRIPTION)

    @property
    def _plan(self) -> dict | None:
        return (self.vehicle_data or {}).get("battery_preheating_plan")

    @property
    def native_value(self) -> time | None:
        plan = self._plan
        if plan is None:
            return None
        try:
            return datetime.strptime(plan["endData"], END_DATA_FORMAT).time()
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def available(self) -> bool:
        return (
            super().available
            and bool(self.coordinator.api.control_private_key)
            and self.native_value is not None
        )

    async def async_set_value(self, value: time) -> None:
        plan = self._plan
        if plan is None:
            raise HomeAssistantError("Mazda did not return a battery-preheating plan")
        try:
            await self.coordinator.api.async_update_battery_preheating(
                self.vehicle.vehicle_id,
                plan["planId"],
                plan["planType"],
                upcoming_end_data(dt_util.now(), value),
            )
        except (KeyError, MazdaApiError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
            raise HomeAssistantError(f"Mazda rejected the {self.name} command: {err}") from err
        await self.coordinator.async_refresh_until(lambda: self.native_value == value)


class Mazda6eChargeScheduleTime(Mazda6eEntity, TimeEntity):
    """Represent the start or end time of the first charging schedule plan."""

    @property
    def _is_start(self) -> bool:
        return self.entity_description.key == "charge_schedule_start"

    @property
    def native_value(self) -> time | None:
        plan = first_charge_plan(self.vehicle_data)
        if plan is None:
            return None
        if self._is_start:
            return parse_plan_time(plan.get("startTime"))
        return parse_plan_time(plan.get("endTime")) if plan_end_enabled(plan) else None

    @property
    def available(self) -> bool:
        return (
            super().available
            and bool(self.coordinator.api.control_private_key)
            and first_charge_plan(self.vehicle_data) is not None
        )

    async def async_set_value(self, value: time) -> None:
        plan = first_charge_plan(self.vehicle_data)
        if plan is None:
            raise HomeAssistantError("Create a charging schedule in the Mazda app first")
        value = value.replace(second=0, microsecond=0)
        start = parse_plan_time(plan.get("startTime"))
        end = parse_plan_time(plan.get("endTime")) if plan_end_enabled(plan) else None
        if self._is_start:
            start = value
        else:
            end = value
        if start is None:
            raise HomeAssistantError("Mazda did not return the charging schedule start time")
        try:
            await self.coordinator.api.async_modify_charge_plan(
                self.vehicle.vehicle_id,
                plan,
                format_plan_time(start),
                format_plan_time(end) if end else "0000",
                end_enabled=end is not None,
                time_zone=plan_time_zone(dt_util.now()),
            )
        except (KeyError, MazdaApiError, RuntimeError, TimeoutError, ValueError) as err:
            raise HomeAssistantError(f"Mazda rejected the {self.name} command: {err}") from err
        await self.coordinator.async_refresh_until(lambda: self.native_value == value)
