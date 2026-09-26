import logging

from datetime import datetime, timezone

_LOGGER = logging.getLogger(__name__)


def speed_value(data):
    status = data.get("status") or {}
    vehicle_status = status.get("vehicleStatus") or {}
    speed = vehicle_status.get("speed")

    if speed is None:
        return None

    try:
        speed = float(speed)
    except (TypeError, ValueError):
        return None

    if speed < 0 or speed > 250:
        _LOGGER.debug("Discarding implausible speed value: %s", speed)
        return None

    return speed


def temperature(raw):
    if raw is None:
        return None

    try:
        raw = float(raw)
    except (TypeError, ValueError):
        return None

    if raw == 0:
        return None

    return raw / 10


# 0x1FFF is reported by the API when no charge time estimate is available
INVALID_CHARGE_TIME = 8191


def remaining_charge_time(raw):
    if raw is None:
        return None

    try:
        raw = float(raw)
    except (TypeError, ValueError):
        return None

    if raw >= INVALID_CHARGE_TIME:
        return None

    return raw


def timestamp_ms(raw):
    """Convert an epoch-milliseconds value from the API into an aware datetime."""
    if raw is None:
        return None

    try:
        raw = int(raw)
    except (TypeError, ValueError):
        return None

    if raw <= 0:
        return None

    return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
