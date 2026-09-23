from dataclasses import dataclass, field
from enum import IntEnum


@dataclass
class Mazda6eVehicle:
    vehicle_id: int
    vin: str
    model_name: str
    car_name: str | None = None
    plate_number: str | None = None
    series_name: str | None = None
    functions: set[str] = field(default_factory=set)

    def supports(self, *codes: str) -> bool:
        """Whether the vehicle advertises all of the given function-config codes."""
        return not self.functions or all(code in self.functions for code in codes)


class ChargeConnectionStatus(IntEnum):
    DISCONNECTED = 0
    CONNECTED = 1


class ChargeStatus(IntEnum):
    UNKNOWN = -1
    NOT_CHARGING = 0
    COMPLETED = 4
    CHARGING = 6
    PAUSED = 7

    @classmethod
    def safe_name(cls, value: int | None) -> str:
        try:
            return cls(value).name
        except (ValueError, TypeError):
            return "UNKNOWN"


class SeatStatusMode(IntEnum):
    UNKNOWN = -1
    OFF = 0
    HEATING = 1
    FAN = 2

    @classmethod
    def safe_name(cls, value: int | None) -> str:
        try:
            return cls(value).name
        except (ValueError, TypeError):
            return "UNKNOWN"


class PowerStatus(IntEnum):
    """vehicleStatus.powerStatus, observed as 2 while remote climate was running."""

    UNKNOWN = -1
    OFF = 0
    ACCESSORY = 1
    ON = 2

    @classmethod
    def safe_name(cls, value: int | None) -> str:
        try:
            return cls(value).name
        except (ValueError, TypeError):
            return "UNKNOWN"


class VehicleStatus(IntEnum):
    UNKNOWN = -1
    DRIVING = 1
    PARKED = 2

    @classmethod
    def safe_name(cls, value: int | None) -> str:
        try:
            return cls(value).name
        except (ValueError, TypeError):
            return "UNKNOWN"


# door.driverLock / door.passengerLock report 1 right after a remote unlock
LOCK_UNLOCKED = 1
