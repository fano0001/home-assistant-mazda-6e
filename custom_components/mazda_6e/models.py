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
