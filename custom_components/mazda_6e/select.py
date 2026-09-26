"""Seat heat and ventilation controls for Mazda 6e vehicles."""

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MazdaApiError
from .const import DOMAIN
from .entity import Mazda6eEntity

OPTIONS = ("Off", "Low", "Medium", "High")
LEVELS = {option: level for level, option in enumerate(OPTIONS)}


@dataclass(frozen=True, kw_only=True)
class Mazda6eSeatDescription(SelectEntityDescription):
    """Description of a captured Mazda seat control."""

    control: str
    position: str
    status_key: str


SEAT_CONTROLS = (
    Mazda6eSeatDescription(key="driver_seat_heat", translation_key="driver_seat_heat", options=OPTIONS, control="heat", position="master", status_key="heatStatus", icon="mdi:car-seat-heater"),
    Mazda6eSeatDescription(key="passenger_seat_heat", translation_key="passenger_seat_heat", options=OPTIONS, control="heat", position="copilot", status_key="heatStatus", icon="mdi:car-seat-heater"),
    Mazda6eSeatDescription(key="driver_seat_ventilation", translation_key="driver_seat_ventilation", options=OPTIONS, control="wind", position="master", status_key="ventStatus", icon="mdi:car-seat-cooler"),
    Mazda6eSeatDescription(key="passenger_seat_ventilation", translation_key="passenger_seat_ventilation", options=OPTIONS, control="wind", position="copilot", status_key="ventStatus", icon="mdi:car-seat-cooler"),
)


def _supports(vehicle, description: Mazda6eSeatDescription) -> bool:
    codes = {
        ("heat", "master"): ("DriverSeatHeaterSW",),
        ("heat", "copilot"): ("FronSeatHeaterSW",),
        ("wind", "master"): ("DriverSeatVentilatorSW",),
        ("wind", "copilot"): ("FronSeatVentilationSW",),
    }
    return vehicle.supports(*codes[(description.control, description.position)])


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Set up captured front-seat controls."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for item in coordinator.data.values():
        vehicle = item["vehicle"]
        if "seat" not in (item.get("status") or {}):
            continue
        entities.extend(
            Mazda6eSeatSelect(coordinator, vehicle, description)
            for description in SEAT_CONTROLS
            if _supports(vehicle, description)
        )
    async_add_entities(entities)


class Mazda6eSeatSelect(Mazda6eEntity, SelectEntity):
    """Represent a captured Mazda front-seat heat or ventilation mode."""

    entity_description: Mazda6eSeatDescription

    def __init__(self, coordinator, vehicle, description) -> None:
        super().__init__(coordinator, vehicle, description)

    @property
    def current_option(self) -> str | None:
        seat_key = "leftFront" if self.entity_description.position == "master" else "rightFront"
        try:
            level = int(self.vehicle_data["status"]["seat"][seat_key][self.entity_description.status_key])
        except (KeyError, TypeError, ValueError):
            return None
        return OPTIONS[level] if level in range(len(OPTIONS)) else None

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.api.control_private_key)

    async def async_select_option(self, option: str) -> None:
        level = LEVELS[option]
        try:
            await self.coordinator.api.async_set_seat_mode(
                self.vehicle.vehicle_id,
                self.entity_description.control,
                self.entity_description.position,
                enabled=level > 0,
                level=max(level, 1),
            )
        except (MazdaApiError, RuntimeError, TimeoutError) as err:
            raise HomeAssistantError(f"Mazda rejected the {self.name} command: {err}") from err
        await self.coordinator.async_refresh_until(lambda: self.current_option == option)