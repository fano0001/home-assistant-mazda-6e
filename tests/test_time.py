"""Offline battery-preheating departure-time entity tests."""

import asyncio
from datetime import time
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock


ROOT = Path(__file__).parents[1] / "custom_components" / "mazda_6e"


def load_time_module(monkeypatch):
    """Load the time platform without installing Home Assistant."""
    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    class EntityDescription:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Mazda6eEntity(CoordinatorEntity):
        def __init__(self, coordinator, vehicle, description):
            super().__init__(coordinator)
            self.vehicle = vehicle
            self.entity_description = description
            self.name = description.key

        @property
        def vehicle_data(self):
            return self.coordinator.data.get(self.vehicle.vehicle_id)

    modules = {name: ModuleType(name) for name in (
        "homeassistant", "homeassistant.components", "homeassistant.components.time",
        "homeassistant.config_entries", "homeassistant.core", "homeassistant.exceptions",
        "homeassistant.helpers", "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform", "homeassistant.helpers.update_coordinator",
        "time_test", "time_test.api", "time_test.const", "time_test.entity",
    )}
    modules["time_test"].__path__ = [str(ROOT)]
    modules["time_test.const"].DOMAIN = "mazda_6e"
    modules["time_test.api"].MazdaApiError = type("MazdaApiError", (Exception,), {})
    modules["time_test.entity"].Mazda6eEntity = Mazda6eEntity
    modules["homeassistant.components.time"].TimeEntity = object
    modules["homeassistant.components.time"].TimeEntityDescription = EntityDescription
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.exceptions"].HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    modules["homeassistant.helpers.entity"].EntityCategory = SimpleNamespace(CONFIG="config")
    modules["homeassistant.helpers.entity_platform"].AddConfigEntryEntitiesCallback = object
    modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = CoordinatorEntity
    for name, value in modules.items():
        monkeypatch.setitem(sys.modules, name, value)
    spec = importlib.util.spec_from_file_location("time_test.time", ROOT / "time.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_departure_time_state_and_action(monkeypatch):
    """Keep the server-provided date and replace only its time component."""
    module = load_time_module(monkeypatch)
    api = SimpleNamespace(
        control_private_key="private-key",
        async_update_battery_preheating=AsyncMock(),
    )
    vehicle = SimpleNamespace(vehicle_id=123)
    coordinator = SimpleNamespace(
        api=api,
        data={123: {"battery_preheating_plan": {
            "planId": 7,
            "planType": 0,
            "endData": "20260928053000",
        }}},
        last_update_success=True,
        async_refresh_until=AsyncMock(),
    )
    entity = module.Mazda6eBatteryPreheatingDepartureTime(coordinator, vehicle)

    assert entity.native_value == time(5, 30)
    assert entity.available is True
    asyncio.run(entity.async_set_value(time(6, 0)))

    api.async_update_battery_preheating.assert_awaited_once_with(
        123, 7, 0, "20260928060000",
    )
    coordinator.async_refresh_until.assert_awaited_once()
