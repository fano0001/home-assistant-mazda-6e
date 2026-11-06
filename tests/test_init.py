"""Offline config entry setup and update listener tests."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock


ROOT = Path(__file__).parents[1] / "custom_components" / "mazda_6e"


def load_init_module(monkeypatch):
    """Load the integration entry point without installing Home Assistant."""
    class Mazda6EApi:
        def __init__(self, session, token, refresh, deviceid, **kwargs):
            self.token_update_callback = kwargs.get("token_update_callback")

    class Mazda6eCoordinator:
        def __init__(self, hass, config_entry, api):
            self.api = api
            self.async_load_trips = AsyncMock()
            self.async_config_entry_first_refresh = AsyncMock()

    modules = {name: ModuleType(name) for name in (
        "homeassistant", "homeassistant.config_entries", "homeassistant.const", "homeassistant.core",
        "homeassistant.exceptions", "homeassistant.helpers", "homeassistant.helpers.aiohttp_client",
        "homeassistant.helpers.config_validation", "homeassistant.helpers.typing",
        "init_test.api", "init_test.const", "init_test.coordinator", "init_test.services",
    )}
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.const"].Platform = SimpleNamespace(**{
        name: name.lower() for name in (
            "BINARY_SENSOR", "BUTTON", "CLIMATE", "COVER", "DEVICE_TRACKER", "LOCK",
            "NUMBER", "SENSOR", "SELECT", "SWITCH", "TIME",
        )
    })
    modules["homeassistant.const"].CONF_REGION = "region"
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.exceptions"].ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    modules["homeassistant.helpers"].aiohttp_client = modules["homeassistant.helpers.aiohttp_client"]
    modules["homeassistant.helpers"].config_validation = modules["homeassistant.helpers.config_validation"]
    modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda hass: None
    modules["homeassistant.helpers.config_validation"].config_entry_only_config_schema = lambda domain: None
    modules["homeassistant.helpers.typing"].ConfigType = dict
    modules["init_test.api"].Mazda6EApi = Mazda6EApi
    modules["init_test.const"].CONF_CONTROL_PIN = "control_pin"
    modules["init_test.const"].DOMAIN = "mazda_6e"
    modules["init_test.const"].REGION_EUROPE = "europe"
    modules["init_test.const"].REGION_ASIA = "asia"
    modules["init_test.coordinator"].Mazda6eCoordinator = Mazda6eCoordinator
    modules["init_test.services"].async_setup_services = lambda hass: None
    for name, value in modules.items():
        monkeypatch.setitem(sys.modules, name, value)
    spec = importlib.util.spec_from_file_location(
        "init_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def make_hass_and_entry():
    listeners = []
    entry = SimpleNamespace(
        entry_id="entry",
        data={"token": "old-token", "refresh": "old-refresh", "deviceid": "device"},
        options={"battery_capacity": 68.8},
        add_update_listener=lambda listener: listeners.append(listener) or (lambda: None),
        async_on_unload=lambda unsub: None,
    )

    def update_entry(config_entry, data=None, options=None):
        if data is not None:
            config_entry.data = data
        if options is not None:
            config_entry.options = options
        for listener in listeners:
            asyncio.run(listener(hass, config_entry))

    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(
            async_update_entry=update_entry,
            async_forward_entry_setups=AsyncMock(),
            async_reload=AsyncMock(),
        ),
    )
    return hass, entry


def test_token_refresh_does_not_reload_but_options_change_does(monkeypatch):
    """Persisting refreshed API tokens must not bounce the whole integration."""
    module = load_init_module(monkeypatch)
    hass, entry = make_hass_and_entry()

    assert asyncio.run(module.async_setup_entry(hass, entry)) is True
    coordinator = hass.data["mazda_6e"]["entry"]

    coordinator.api.token_update_callback("new-token", "new-refresh")

    assert entry.data["token"] == "new-token"
    assert entry.data["refresh"] == "new-refresh"
    hass.config_entries.async_reload.assert_not_awaited()

    hass.config_entries.async_update_entry(entry, options={"battery_capacity": 70.0})

    hass.config_entries.async_reload.assert_awaited_once_with("entry")
