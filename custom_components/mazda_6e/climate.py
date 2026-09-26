"""Climate controls for Mazda 6e vehicles."""

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityDescription,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MazdaApiError
from .const import DOMAIN
from .entity import Mazda6eEntity
from .helpers.validators import temperature

DESCRIPTION = ClimateEntityDescription(
    key="cabin_climate",
    translation_key="cabin_climate",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up cabin climate entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        Mazda6eClimate(coordinator, item["vehicle"])
        for item in coordinator.data.values()
        if item["vehicle"].supports("ACSW") and "hvac" in item["status"]
    )


class Mazda6eClimate(Mazda6eEntity, ClimateEntity):
    """Control remote cabin climate through Mazda's captured cloud endpoint."""

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _attr_min_temp = 16
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, vehicle) -> None:
        super().__init__(coordinator, vehicle, DESCRIPTION)

    @property
    def supported_features(self) -> ClimateEntityFeature:
        return ClimateEntityFeature.TARGET_TEMPERATURE

    @property
    def hvac_mode(self) -> HVACMode | None:
        try:
            return HVACMode.HEAT_COOL if self.vehicle_data["status"]["hvac"]["acStatus"] else HVACMode.OFF
        except (KeyError, TypeError):
            return None

    @property
    def current_temperature(self) -> float | None:
        try:
            return temperature(self.vehicle_data["status"]["hvac"]["insideTemp"])
        except (KeyError, TypeError):
            return None

    @property
    def target_temperature(self) -> float | None:
        try:
            return temperature(self.vehicle_data["status"]["hvac"]["remoteTemp"])
        except (KeyError, TypeError):
            return None

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.api.control_private_key)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._async_set_climate(hvac_mode == HVACMode.HEAT_COOL, self.target_temperature or 21)

    async def async_set_temperature(self, **kwargs) -> None:
        await self._async_set_climate(True, kwargs["temperature"])

    async def _async_set_climate(self, enabled: bool, target_temperature: float) -> None:
        try:
            await self.coordinator.api.async_set_air_conditioner(
                self.vehicle.vehicle_id, enabled, target_temperature,
            )
        except (MazdaApiError, RuntimeError, TimeoutError) as err:
            raise HomeAssistantError(f"Mazda rejected the climate command: {err}") from err
        await self.coordinator.async_refresh_until(
            lambda: self._climate_state_matches(enabled, target_temperature),
        )

    def _climate_state_matches(self, enabled: bool, target_temperature: float) -> bool:
        expected_mode = HVACMode.HEAT_COOL if enabled else HVACMode.OFF
        if self.hvac_mode != expected_mode:
            return False
        if not enabled:
            return True
        current_temperature = self.target_temperature
        return (
            current_temperature is not None
            and abs(current_temperature - target_temperature) < 0.1
        )