"""Battery-preheating departure time for Mazda 6e vehicles."""

from datetime import datetime, time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MazdaApiError
from .const import DOMAIN
from .entity import Mazda6eEntity

BATTERY_PREHEATING_DEPARTURE_TIME_DESCRIPTION = TimeEntityDescription(
    key="battery_preheating_departure_time",
    translation_key="battery_preheating_departure_time",
    icon="mdi:clock-outline",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up battery-preheating departure-time controls."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        Mazda6eBatteryPreheatingDepartureTime(coordinator, item["vehicle"])
        for item in coordinator.data.values()
        if item.get("battery_preheating_plan") is not None
    )


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
            return datetime.strptime(plan["endData"], "%Y%m%d%H%M%S").time()
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
            current = datetime.strptime(plan["endData"], "%Y%m%d%H%M%S")
            updated = current.replace(
                hour=value.hour,
                minute=value.minute,
                second=value.second,
                microsecond=0,
            ).strftime("%Y%m%d%H%M%S")
            await self.coordinator.api.async_update_battery_preheating(
                self.vehicle.vehicle_id,
                plan["planId"],
                plan["planType"],
                updated,
            )
        except (KeyError, MazdaApiError, RuntimeError, TimeoutError, TypeError, ValueError) as err:
            raise HomeAssistantError(f"Mazda rejected the {self.name} command: {err}") from err
        await self.coordinator.async_refresh_until(lambda: self.native_value == value)
