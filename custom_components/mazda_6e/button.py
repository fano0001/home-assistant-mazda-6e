"""Button controls for Mazda 6e vehicles."""

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MazdaApiError
from .const import DOMAIN
from .entity import Mazda6eEntity

FIND_VEHICLE_DESCRIPTION = ButtonEntityDescription(
    key="find_vehicle",
    translation_key="find_vehicle",
    icon="mdi:car-search",
)

HONK_HORN_DESCRIPTION = ButtonEntityDescription(
    key="honk_horn",
    translation_key="honk_horn",
    icon="mdi:bullhorn",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up vehicle find and horn buttons."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for item in coordinator.data.values():
        vehicle = item["vehicle"]
        if vehicle.supports("FlashHonk"):
            entities.extend(
                (
                    Mazda6eFindVehicleButton(coordinator, vehicle),
                    Mazda6eHonkHornButton(coordinator, vehicle),
                )
            )
    async_add_entities(entities)


class Mazda6eFindVehicleButton(Mazda6eEntity, ButtonEntity):
    """Trigger Mazda's flashing-and-honking find-vehicle command."""

    def __init__(self, coordinator, vehicle) -> None:
        super().__init__(coordinator, vehicle, FIND_VEHICLE_DESCRIPTION)

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.api.control_private_key)

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.async_find_vehicle(self.vehicle.vehicle_id)
        except (MazdaApiError, RuntimeError, TimeoutError) as err:
            raise HomeAssistantError(f"Mazda rejected the Find vehicle command: {err}") from err
        await self.coordinator.async_request_refresh()


class Mazda6eHonkHornButton(Mazda6eEntity, ButtonEntity):
    """Trigger Mazda's captured horn command."""

    def __init__(self, coordinator, vehicle) -> None:
        super().__init__(coordinator, vehicle, HONK_HORN_DESCRIPTION)

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.api.control_private_key)

    async def async_press(self) -> None:
        try:
            await self.coordinator.api.async_flash_honk(self.vehicle.vehicle_id, action_type=3)
        except (MazdaApiError, RuntimeError, TimeoutError) as err:
            raise HomeAssistantError(f"Mazda rejected the Horn command: {err}") from err
        await self.coordinator.async_request_refresh()