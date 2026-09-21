"""Offline lock entity tests with a minimal Home Assistant facade."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock


ROOT = Path(__file__).parents[1] / "custom_components" / "mazda_6e"


def load_lock_module(monkeypatch):
    """Load the lock platform without installing Home Assistant."""
    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    modules = {name: ModuleType(name) for name in (
        "homeassistant", "homeassistant.components", "homeassistant.components.lock",
        "homeassistant.config_entries", "homeassistant.core", "homeassistant.helpers",
        "homeassistant.helpers.device_registry", "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.update_coordinator", "lock_test", "lock_test.const",
    )}
    modules["lock_test"].__path__ = [str(ROOT)]
    modules["lock_test.const"].DOMAIN = "mazda_6e"
    modules["homeassistant.components.lock"].LockEntity = object
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.helpers.device_registry"].DeviceInfo = lambda **kwargs: kwargs
    modules["homeassistant.helpers.entity_platform"].AddConfigEntryEntitiesCallback = object
    modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = CoordinatorEntity
    for name, value in modules.items():
        monkeypatch.setitem(sys.modules, name, value)
    spec = importlib.util.spec_from_file_location("lock_test.lock", ROOT / "lock.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_lock_state_availability_and_actions(monkeypatch):
    """The entity requires control credentials and delegates both actions."""
    module = load_lock_module(monkeypatch)
    api = SimpleNamespace(
        control_private_key=None,
        control_pin=None,
        async_lock=AsyncMock(),
        async_unlock=AsyncMock(),
    )
    vehicle = SimpleNamespace(vehicle_id=123, vin="TESTVIN")
    coordinator = SimpleNamespace(
        api=api,
        data={123: {"status": {"door": {"driverLock": 0, "passengerLock": 0}}}},
        last_update_success=True,
        async_request_refresh=AsyncMock(),
    )
    entity = module.Mazda6eDoorLock(coordinator, vehicle)
    assert entity.is_locked is True
    assert entity.available is False
    api.control_private_key = "private-key"
    api.control_pin = "123456"
    assert entity.available is True

    async def run():
        await entity.async_unlock()
        await entity.async_lock()

    asyncio.run(run())
    api.async_unlock.assert_awaited_once_with(123)
    api.async_lock.assert_awaited_once_with(123)
    assert coordinator.async_request_refresh.await_count == 2
