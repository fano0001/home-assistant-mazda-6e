from dataclasses import dataclass
from enum import IntEnum


@dataclass
class Mazda6eVehicle:
    vehicle_id: int
    vin: str
    model_name: str


class ChargeConnectionStatus(IntEnum):
    DISCONNECTED = 1
    CONNECTED = 3


class ChargeStatus(IntEnum):
    NOT_CHARGING = 0
    COMPLETED = 4
    CHARGING = 6
    PAUSED = 7
