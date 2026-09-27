"""Rules for replacing read-only entities with writable controls."""

from __future__ import annotations


def control_replaces_read_only_entity(
    platform: str,
    key: str,
    functions: set[str],
    *,
    has_control_key: bool,
    has_control_pin: bool,
) -> bool:
    """Return whether an available control exposes the same state."""
    if not has_control_key:
        return False

    entity = (platform, key)
    required_function = {
        ("sensor", "charge_target_soc"): "BatteryMaxSoc",
        ("binary_sensor", "air_conditioning"): "ACSW",
        ("binary_sensor", "defrost"): "ACFrontDefrosterSW",
        ("binary_sensor", "steering_wheel_heater"): "SteeringWheelSW",
    }.get(entity)
    if required_function is not None:
        return not functions or required_function in functions

    if entity == ("binary_sensor", "trunk"):
        return has_control_pin and (
            not functions or bool({"TrunkAutoSW", "TrunkUnlock"} & functions)
        )

    return False
