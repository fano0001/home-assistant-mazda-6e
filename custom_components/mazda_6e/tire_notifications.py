"""Persistent notifications for the vehicle's tire warnings."""

from hashlib import sha256
from math import isfinite

from homeassistant.components import persistent_notification

from .const import DOMAIN

_TEXT = {
    "en": {
        "wheels": ("left front", "right front", "left rear", "right rear"),
        "low": "Low tire pressure", "warning": "Tire warning",
        "pressure": "Last reported pressure: {pressure} bar.",
        "code": "The vehicle reports tire warning code {code}.",
    },
    "da": {
        "wheels": ("venstre forhjul", "højre forhjul", "venstre baghjul", "højre baghjul"),
        "low": "Lavt dæktryk", "warning": "Dækadvarsel",
        "pressure": "Senest rapporterede tryk: {pressure} bar.",
        "code": "Bilen melder dækadvarsel med kode {code}.",
    },
    "de": {
        "wheels": ("vorne links", "vorne rechts", "hinten links", "hinten rechts"),
        "low": "Niedriger Reifendruck", "warning": "Reifenwarnung",
        "pressure": "Zuletzt gemeldeter Druck: {pressure} bar.",
        "code": "Das Fahrzeug meldet die Reifenwarnung {code}.",
    },
}
_WHEELS = ("leftFront", "rightFront", "leftBack", "rightBack")


class TireNotifications:
    def __init__(self, hass, entry_id):
        self.hass = hass
        self.entry_id = entry_id
        self._last = {}

    def update(self, vehicle_id, vehicle, status):
        language = self.hass.config.language.split("-")[0]
        text = _TEXT.get(language, _TEXT["en"])
        identity = sha256(f"{self.entry_id}:{vehicle_id}".encode()).hexdigest()[:16]
        name = getattr(vehicle, "car_name", None) or getattr(vehicle, "model_name", None) or "Mazda 6e"
        tires = (status or {}).get("tire") or {}
        for index, wheel in enumerate(_WHEELS):
            tire = tires.get(wheel) or {}
            code = tire.get("status")
            if isinstance(code, bool) or code not in (0, 1, 2):
                continue
            notification_id = f"{DOMAIN}_{identity}_tire_{wheel}"
            if code == 0:
                if self._last.get(notification_id) != (0, None):
                    persistent_notification.async_dismiss(self.hass, notification_id)
                self._last[notification_id] = (0, None)
                continue
            pressure = tire.get("pressure")
            if isinstance(pressure, bool) or not isinstance(pressure, (int, float)) or not isfinite(pressure):
                pressure = None
            value = (code, pressure)
            if self._last.get(notification_id) == value:
                continue
            title = f"{name}: {text['low'] if code == 2 else text['warning']} - {text['wheels'][index]}"
            message = text["code"].format(code=code)
            if pressure is not None:
                formatted = f"{pressure / 100:.2f}"
                if language in ("da", "de"):
                    formatted = formatted.replace(".", ",")
                message += "\n\n" + text["pressure"].format(pressure=formatted)
            persistent_notification.async_create(
                self.hass, message, title=title, notification_id=notification_id,
            )
            self._last[notification_id] = value
