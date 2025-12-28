import logging

_LOGGER = logging.getLogger(__name__)


def speed_value(data):
    speed = data["status"]["vehicleStatus"].get("speed")

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
