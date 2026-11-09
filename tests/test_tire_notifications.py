"""Tire warning notification lifecycle, without Home Assistant services."""

from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import sys

import pytest

from test_asia_status import load_module, modules


@pytest.fixture
def notifier(modules, monkeypatch):
    components = ModuleType("homeassistant.components")
    notifications = ModuleType("homeassistant.components.persistent_notification")
    notifications.async_create = Mock()
    notifications.async_dismiss = Mock()
    components.persistent_notification = notifications
    monkeypatch.setitem(sys.modules, "homeassistant.components", components)
    monkeypatch.setitem(sys.modules, "homeassistant.components.persistent_notification", notifications)
    registry = ModuleType("homeassistant.helpers.device_registry")
    registry.async_get = Mock(return_value=object())
    registry.async_entries_for_config_entry = Mock(return_value=[])
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.device_registry", registry)
    helpers = ModuleType("homeassistant.helpers")
    helpers.device_registry = registry
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    module = load_module(monkeypatch, "asia_test.tire_notifications", "tire_notifications.py")
    hass = SimpleNamespace(config=SimpleNamespace(language="en"))
    return module.TireNotifications(hass, "entry-one"), notifications, module


@pytest.mark.parametrize("wheel", ["leftFront", "rightFront", "leftBack", "rightBack"])
def test_warning_updates_and_clears_with_same_id(notifier, wheel):
    manager, api, _ = notifier
    vehicle = SimpleNamespace(car_name="Takumi")
    def update(code, pressure=216):
        manager.update(1, vehicle, {"tire": {wheel: {"status": code, "pressure": pressure}}})
    update(2)
    first = api.async_create.call_args
    assert "Low tire pressure" in first.kwargs["title"]
    assert first.args[1] == "Last reported pressure: 2.16 bar."
    update(2)
    assert api.async_create.call_count == 1
    update(1, 218)
    assert "Tire warning" in api.async_create.call_args.kwargs["title"]
    assert api.async_create.call_args.args[1] == (
        "The vehicle reports tire warning code 1.\n\nLast reported pressure: 2.18 bar."
    )
    assert "Low tire pressure" not in api.async_create.call_args.kwargs["title"]
    assert api.async_create.call_args.kwargs["notification_id"] == first.kwargs["notification_id"]
    for status in (None, {}, {"tire": None}, {"tire": {wheel: {"status": 3}}}):
        manager.update(1, vehicle, status)
    api.async_dismiss.assert_not_called()
    update(0)
    api.async_dismiss.assert_called_once_with(manager.hass, first.kwargs["notification_id"])
    update(0)
    assert api.async_dismiss.call_count == 1
    update(2)
    assert api.async_create.call_count == 3


def test_ids_survive_reload_and_separate_vehicles_entries_and_wheels(notifier):
    manager, api, module = notifier
    status = {"tire": {wheel: {"status": 2} for wheel in ("leftFront", "rightFront")}}
    manager.update(1, None, status)
    ids = [call.kwargs["notification_id"] for call in api.async_create.call_args_list]
    assert len(set(ids)) == 2
    reloaded = module.TireNotifications(manager.hass, "entry-one")
    reloaded.update(1, None, status)
    assert [call.kwargs["notification_id"] for call in api.async_create.call_args_list[2:]] == ids
    manager.update(2, None, status)
    module.TireNotifications(manager.hass, "entry-two").update(1, None, status)
    all_ids = [call.kwargs["notification_id"] for call in api.async_create.call_args_list]
    assert len(set(all_ids)) == 6
    reloaded = module.TireNotifications(manager.hass, "entry-one")
    reloaded.update(1, None, {"tire": {"leftFront": {"status": 0}}})
    api.async_dismiss.assert_called_once_with(manager.hass, ids[0])


@pytest.mark.parametrize("pressure", [None, True, float("nan"), float("inf")])
def test_invalid_pressure_does_not_hide_warning(notifier, pressure):
    manager, api, _ = notifier
    manager.update(1, None, {"tire": {"rightFront": {"status": 2, "pressure": pressure}}})
    assert "Low tire pressure" in api.async_create.call_args.kwargs["title"]
    assert api.async_create.call_args.args[1] == ""


@pytest.mark.parametrize("language,label,number", [
    ("da", "Lavt dæktryk", "2,16"),
    ("de", "Niedriger Reifendruck", "2,16"),
    ("fr", "Low tire pressure", "2.16"),
])
def test_notification_language(notifier, language, label, number):
    manager, api, _ = notifier
    manager.hass.config.language = language
    manager.update(1, None, {"tire": {"rightFront": {"status": 2, "pressure": 216}}})
    assert label in api.async_create.call_args.kwargs["title"]
    assert number in api.async_create.call_args.args[1]


def test_device_name_and_user_rename_update_existing_notification(notifier):
    manager, api, _ = notifier
    registry = sys.modules["homeassistant.helpers.device_registry"]
    vehicle = SimpleNamespace(car_name="API nickname", model_name="Mazda 6e")
    status = {"tire": {"rightFront": {"status": 2, "pressure": 216}}}
    manager.update(1, vehicle, status)
    first_id = api.async_create.call_args.kwargs["notification_id"]
    device = SimpleNamespace(identifiers={("mazda_6e", "1")}, name_by_user=None, name="Garage car")
    registry.async_entries_for_config_entry.return_value = [
        SimpleNamespace(identifiers={("mazda_6e", "2")}, name_by_user="Other car", name="Wrong"), device,
    ]
    manager.update(1, vehicle, status)
    assert api.async_create.call_args.kwargs["title"].startswith("Garage car:")
    device.name_by_user = "Takumi"
    manager.update(1, vehicle, status)
    assert api.async_create.call_args.kwargs["title"].startswith("Takumi:")
    assert api.async_create.call_args.kwargs["notification_id"] == first_id
    registry.async_entries_for_config_entry.assert_called_with(registry.async_get.return_value, "entry-one")
    assert api.async_create.call_count == 3


def test_coordinator_listener_updates_and_unsubscribes(notifier):
    manager, api, module = notifier
    clear = {1: {"vehicle": None, "status": {"tire": {"rightFront": {"status": 0}}}}}
    coordinator = SimpleNamespace(
        last_update_success=True,
        data={1: {"vehicle": None, "status": {"tire": {"rightFront": {"status": 2, "pressure": 216}}}}},
        async_add_listener=Mock(return_value=Mock()),
    )
    entry = SimpleNamespace(entry_id="entry-one", async_on_unload=Mock())
    module.async_setup_tire_notifications(manager.hass, entry, coordinator)
    api.async_create.assert_called_once()
    callback = coordinator.async_add_listener.call_args.args[0]
    entry.async_on_unload.assert_called_once_with(coordinator.async_add_listener.return_value)
    coordinator.last_update_success = False
    coordinator.data = clear
    callback()
    api.async_dismiss.assert_not_called()
    coordinator.last_update_success = True
    coordinator.data = None
    callback()
    api.async_dismiss.assert_not_called()
    coordinator.data = clear
    callback()
    api.async_dismiss.assert_called_once()


def test_listener_ignores_warning_data_from_failed_refresh(notifier):
    manager, api, module = notifier
    coordinator = SimpleNamespace(
        last_update_success=False,
        data={1: {"vehicle": None, "status": {"tire": {"rightFront": {"status": 2}}}}},
        async_add_listener=Mock(),
    )
    module.async_setup_tire_notifications(
        manager.hass, SimpleNamespace(entry_id="entry-one", async_on_unload=Mock()), coordinator,
    )
    api.async_create.assert_not_called()
    coordinator.last_update_success = True
    coordinator.async_add_listener.call_args.args[0]()
    api.async_create.assert_called_once()
