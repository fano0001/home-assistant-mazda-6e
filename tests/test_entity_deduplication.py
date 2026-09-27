"""Tests for replacing duplicate read-only entities with controls."""

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "mazda_6e"
    / "entity_deduplication.py"
)
SPEC = importlib.util.spec_from_file_location("entity_deduplication_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
control_replaces_read_only_entity = MODULE.control_replaces_read_only_entity


def is_replaced(
    platform: str,
    key: str,
    functions: set[str],
    *,
    has_control_key: bool = True,
    has_control_pin: bool = False,
) -> bool:
    """Call the deduplication rule with concise test defaults."""
    return control_replaces_read_only_entity(
        platform,
        key,
        functions,
        has_control_key=has_control_key,
        has_control_pin=has_control_pin,
    )


def test_exact_duplicates_are_replaced_by_supported_controls():
    """Controls replace the five read-only entities backed by the same fields."""
    assert is_replaced("sensor", "charge_target_soc", {"BatteryMaxSoc"})
    assert is_replaced("binary_sensor", "air_conditioning", {"ACSW"})
    assert is_replaced("binary_sensor", "defrost", {"ACFrontDefrosterSW"})
    assert is_replaced(
        "binary_sensor", "steering_wheel_heater", {"SteeringWheelSW"}
    )
    assert is_replaced(
        "binary_sensor", "trunk", {"TrunkUnlock"}, has_control_pin=True
    )


def test_read_only_entities_remain_as_fallbacks():
    """Do not remove telemetry when its corresponding control is unavailable."""
    assert not is_replaced(
        "sensor", "charge_target_soc", {"BatteryMaxSoc"}, has_control_key=False
    )
    assert not is_replaced("binary_sensor", "defrost", {"ACSW"})
    assert not is_replaced("binary_sensor", "trunk", {"TrunkUnlock"})


def test_non_duplicate_entities_are_retained():
    """Related entities with different information are not deduplicated."""
    assert not is_replaced("sensor", "chargeStatus", set())
    assert not is_replaced("binary_sensor", "is_charging", set())
    assert not is_replaced("sensor", "temperature_inside", {"ACSW"})
    assert not is_replaced("sensor", "seat_status_front_left", {"DriverSeatHeaterSW"})
    assert not is_replaced("binary_sensor", "front_left_window", {"WindowSW"})
    assert not is_replaced("binary_sensor", "driver_lock", set())
