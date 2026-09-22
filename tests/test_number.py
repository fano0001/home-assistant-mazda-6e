"""Offline charge-limit number entity tests."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock


ROOT = Path(__file__).parents[1] / "custom_components" / "mazda_6e"


def load_number_module(monkeypatch):
    """Load the number platform without installing Home Assistant."""
    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    class NumberMode:
        SLIDER = "slider"

    class NumberEntityDescription:
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
        "homeassistant", "homeassistant.components", "homeassistant.components.number",
        "homeassistant.config_entries", "homeassistant.const", "homeassistant.core", "homeassistant.exceptions",
        "homeassistant.helpers", "homeassistant.helpers.device_registry", "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform", "homeassistant.helpers.update_coordinator",
        "number_test", "number_test.api", "number_test.const", "number_test.entity",
    )}
    modules["number_test"].__path__ = [str(ROOT)]
    modules["number_test.const"].DOMAIN = "mazda_6e"
    modules["number_test.api"].MazdaApiError = type("MazdaApiError", (Exception,), {})
    modules["number_test.entity"].Mazda6eEntity = Mazda6eEntity
    modules["homeassistant.components.number"].NumberEntity = object
    modules["homeassistant.components.number"].NumberEntityDescription = NumberEntityDescription
    modules["homeassistant.components.number"].NumberMode = NumberMode
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.const"].PERCENTAGE = "%"
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.exceptions"].HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    modules["homeassistant.helpers.entity"].EntityCategory = SimpleNamespace(CONFIG="config")
    modules["homeassistant.helpers.device_registry"].DeviceInfo = lambda **kwargs: kwargs
    modules["homeassistant.helpers.entity_platform"].AddConfigEntryEntitiesCallback = object
    modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = CoordinatorEntity
    for name, value in modules.items():
        monkeypatch.setitem(sys.modules, name, value)
    spec = importlib.util.spec_from_file_location("number_test.number", ROOT / "number.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_charge_limit_state_availability_and_action(monkeypatch):
    """The entity reports backend state and delegates changes without a PIN."""
    module = load_number_module(monkeypatch)
    api = SimpleNamespace(
        control_private_key=None,
        async_set_charge_limit=AsyncMock(),
    )
    vehicle = SimpleNamespace(vehicle_id=123, vin="TESTVIN", supports=lambda *codes: True)
    coordinator = SimpleNamespace(
        api=api,
        data={123: {"status": {"charge": {"maxSocPercent": 83}}}},
        last_update_success=True,
        async_refresh_until=AsyncMock(),
    )
    entity = module.Mazda6eChargeLimit(coordinator, vehicle)
    assert entity.native_value == 83
    assert entity.available is False
    api.control_private_key = "private-key"
    assert entity.available is True

    asyncio.run(entity.async_set_native_value(85.0))

    api.async_set_charge_limit.assert_awaited_once_with(123, 85)
    coordinator.async_refresh_until.assert_awaited_once()
