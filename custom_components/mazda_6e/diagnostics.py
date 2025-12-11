from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN

TO_REDACT_INFO = [CONF_EMAIL, CONF_PASSWORD]
TO_REDACT_DATA = ["vin", "id"]


async def async_get_config_entry_diagnostics(
        hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    vehicles = []

    for vehicle_id, entry in coordinator.data.items():
        vehicle_status = entry.get("status", {})
        vehicles.append(
            {
                "vehicle_id": vehicle_id,
                "status": async_redact_data(vehicle_status, TO_REDACT_DATA),
            }
        )

    diagnostics = {
        "info": async_redact_data(config_entry.data, TO_REDACT_INFO),
        "vehicles": vehicles,
    }

    return diagnostics
