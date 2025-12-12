import logging

from homeassistant.config_entries import ConfigEntry, SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers import aiohttp_client
from homeassistant.const import Platform


from .api import Mazda6EApi
from .const import DOMAIN
from .coordinator import Mazda6eCoordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
]

_LOGGER = logging.getLogger(__name__)

async def trigger_reauthentication(hass: HomeAssistant, entry: ConfigEntry):
    """Start reauthentication flow."""
    _LOGGER.warning("Starting reauthentication flow for Mazda 6E")

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
    )

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    _LOGGER.info("Setting up Mazda 6E integration")
    _LOGGER.info("config_entry: %s", config_entry.data)

    mazda6e_api = Mazda6EApi(
        aiohttp_client.async_get_clientsession(hass),
        config_entry.data["token"],
        config_entry.data["refresh"],
        config_entry.data["deviceid"],
    )

    coordinator = Mazda6eCoordinator(hass, config_entry, mazda6e_api)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        if getattr(err, "status", None) in (401, 403):
            _LOGGER.warning("Authentication failed: %s – triggering reauth", err)
            await trigger_reauthentication(hass, config_entry)
            return False

        raise

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    hass.data[DOMAIN].pop(entry.entry_id)

    return True