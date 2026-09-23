"""Switch controls for Mazda 6e vehicles."""

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MazdaApiError
from .const import DOMAIN
from .entity import Mazda6eEntity


@dataclass(frozen=True, kw_only=True)
class Mazda6eSwitchDescription(SwitchEntityDescription):
    """Description of a captured Mazda switch command."""

    state_key: tuple[str, str]


SWITCHES = (
    Mazda6eSwitchDescription(
        key="front_defrost",
        translation_key="front_defrost",
        icon="mdi:car-defrost-front",
        state_key=("hvac", "defrostStatus"),
    ),
    Mazda6eSwitchDescription(
        key="steering_wheel_heat",
        translation_key="steering_wheel_heat",
        icon="mdi:steering",
        state_key=("vehicleStatus", "steeringWheelHeater"),
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Set up captured vehicle switches."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for item in coordinator.data.values():
        vehicle = item["vehicle"]
        status = item.get("status") or {}
        if "hvac" in status and vehicle.supports("ACFrontDefrosterSW"):
            entities.append(Mazda6eControlSwitch(coordinator, vehicle, SWITCHES[0]))
        if "vehicleStatus" in status and vehicle.supports("SteeringWheelSW"):
            entities.append(Mazda6eControlSwitch(coordinator, vehicle, SWITCHES[1]))
    async_add_entities(entities)


class Mazda6eControlSwitch(Mazda6eEntity, SwitchEntity):
    """Represent a captured Mazda on/off vehicle control."""

    entity_description: Mazda6eSwitchDescription

    def __init__(self, coordinator, vehicle, description) -> None:
        super().__init__(coordinator, vehicle, description)

    @property
    def is_on(self) -> bool | None:
        try:
            section, key = self.entity_description.state_key
            return bool(self.vehicle_data["status"][section][key])
        except (KeyError, TypeError):
            return None

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.api.control_private_key)

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        try:
            if self.entity_description.key == "front_defrost":
                await self.coordinator.api.async_set_defrost(self.vehicle.vehicle_id, enabled)
            else:
                await self.coordinator.api.async_set_steering_wheel_heat(self.vehicle.vehicle_id, enabled)
        except (MazdaApiError, RuntimeError, TimeoutError) as err:
            raise HomeAssistantError(f"Mazda rejected the {self.name} command: {err}") from err
        await self.coordinator.async_request_refresh()