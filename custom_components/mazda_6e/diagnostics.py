from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT_CONFIG = [CONF_EMAIL, CONF_PASSWORD, 'email_enc', 'refresh', 'token']
TO_REDACT_DATA = ["vin", "vehicle_id", "token", "access_token", "refresh_token", "session_id"]


async def async_get_config_entry_diagnostics(
        hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    data_entry = hass.data[DOMAIN][config_entry.entry_id]

    vehicles = []

    for vehicle_id, entry in data_entry.data.items():
        vehicle_status = entry.get("status", {})
        vehicles.append(
            {
                "vehicle_id": vehicle_id,
                "status": async_redact_data(vehicle_status, TO_REDACT_DATA),
            }
        )

    diagnostics = {
        "info": async_redact_data(config_entry.data, TO_REDACT_CONFIG),
        "vehicles": vehicles,
    }

    return diagnostics
