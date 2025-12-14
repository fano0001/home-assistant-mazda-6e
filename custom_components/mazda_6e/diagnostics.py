from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

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


async def async_get_device_diagnostics(
        hass: HomeAssistant, config_entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a device."""
    data_entry = hass.data[DOMAIN][config_entry.entry_id]

    vehicleId = next(iter(device.identifiers))[1]

    target_vehicle = None
    for vehicle_id, entry in data_entry.data.items():
        if vehicle_id == int(vehicleId):
            target_vehicle = entry
            break

    if target_vehicle is None:
        raise HomeAssistantError(f"Vehicle with id '{vehicleId}' not found")

    diagnostics_data = {
        "info": async_redact_data(config_entry.data, TO_REDACT_CONFIG),
        "data": async_redact_data(target_vehicle.get("status", {}), TO_REDACT_DATA),
    }

    return diagnostics_data
