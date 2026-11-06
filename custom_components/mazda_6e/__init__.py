import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.const import Platform, CONF_REGION
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import Mazda6EApi
from .const import CONF_CONTROL_PIN, DOMAIN, REGION_EUROPE, REGION_ASIA
from .coordinator import Mazda6eCoordinator
from .services import async_setup_services

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.DEVICE_TRACKER,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TIME,
]

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    _LOGGER.info("Setting up Mazda 6E integration")

    def update_tokens(token: str, refresh: str) -> None:
        hass.config_entries.async_update_entry(
            config_entry,
            data={**config_entry.data, "token": token, "refresh": refresh},
        )

    mazda6e_api = Mazda6EApi(
        aiohttp_client.async_get_clientsession(hass),
        config_entry.data["token"],
        config_entry.data["refresh"],
        config_entry.data["deviceid"],
        control_private_key=config_entry.data.get("control_private_key"),
        control_pin=config_entry.data.get(CONF_CONTROL_PIN),
        region=config_entry.data.get(CONF_REGION, REGION_EUROPE),
        token_update_callback=update_tokens,
    )

    coordinator = Mazda6eCoordinator(hass, config_entry, mazda6e_api)
    coordinator.options_snapshot = dict(config_entry.options)
    await coordinator.async_load_trips()

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        if getattr(err, "status", None) in (401, 403):
            _LOGGER.warning("Authentication failed: %s – triggering reauth", err)
            raise ConfigEntryAuthFailed from err

        raise

    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    config_entry.async_on_unload(config_entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    # Token refreshes rewrite entry data too; only a real options change needs a reload.
    if coordinator is not None and dict(entry.options) == coordinator.options_snapshot:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    hass.data[DOMAIN].pop(entry.entry_id)

    return True
