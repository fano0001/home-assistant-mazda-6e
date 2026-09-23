import asyncio
import aiohttp
import time
import logging

from .const import DEVICE_NAME
from .credential_crypto import decrypt_control_serial, encrypt_credential, sign_control_payload
from .models import Mazda6eVehicle
from homeassistant.exceptions import ConfigEntryAuthFailed

_LOGGER = logging.getLogger(__name__)

BASE = "https://cma-m.iov.changanauto.com.de/cma-app-gw"

HEADERS_BASE = {
    "content-type": "application/json",
    "devicetype": "iPhone",
    "apptype": "IOS",
    "appid": "cma",
    "accept": "*/*",
    "appversion": "V1.2.3",
    "accept-language": "en-US;q=1.0",
    "user-agent": "overseas/1.2.3 (com.mazda.mazda6e; build:1; iOS 27.0.0) Alamofire/5.5.0",
    "language": "en_US",
}


class MazdaLoginError(Exception):
    """Mazda rejected an email and password login request."""

    def __init__(self, code) -> None:
        """Initialize the error with Mazda's non-sensitive response code."""
        self.code = code
        super().__init__(f"Mazda login rejected with code {code}")


class MazdaApiError(Exception):
    """Mazda rejected an authenticated API request."""


def now_ts():
    return str(int(time.time()))


class Mazda6EApi:
    def __init__(self, session: aiohttp.ClientSession, token=None, refresh=None,
                 deviceid=None, control_public_key=None, control_private_key=None,
                 control_pin=None):
        self.session = session
        self.token = token
        self.refresh = refresh
        self.deviceid = deviceid
        self.control_public_key = control_public_key
        self.control_private_key = control_private_key
        self.control_pin = control_pin

    async def _request(self, url: str, headers: dict, body: dict, retry: bool = True):
        """generic request method with token refresh handling"""
        async with self.session.post(url, headers=headers, json=body) as resp:
            raw = await resp.json()

        if raw.get("success") is True:
            return raw

        # token expired
        if raw.get("code") == "APP_1_1_02_004":
            if not retry:
                raise ConfigEntryAuthFailed("Token expired and refresh failed")

            _LOGGER.debug("Token expired -> refreshing token...")
            await self.refresh_token()

            headers["authorization"] = self.token

            # try again once
            return await self._request(url, headers, body, retry=False)
        code = raw.get("code", "unknown")
        message = raw.get("msg", "unknown error")
        raise MazdaApiError(f"Mazda API request rejected ({code}: {message})")

    async def login_email_password(self, email_enc, password_enc):
        if not self.control_public_key:
            raise ValueError("Missing control public key")
        url = f"{BASE}/cma-app-auth/api/login/email-pass-in/v2"
        payload = {
            "loginTime": now_ts(),
            "email": email_enc,
            "password": password_enc,
            "pubKey": self.control_public_key
        }
        headers = {**HEADERS_BASE, "deviceid": self.deviceid}

        async with self.session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()

            if not data.get("success"):
                raise MazdaLoginError(data.get("code"))

            self.token = data["data"]["token"]
            self.refresh = data["data"]["refreshToken"]
            return data["data"]

    async def send_device_login(self, token, email_enc):
        url = f"{BASE}/cma-app-user/api/send-email/device-login/send"
        payload = {
            "email": email_enc,
            "deviceName": DEVICE_NAME,
            "loginTime": now_ts(),
            "type": "1"
        }
        headers = {**HEADERS_BASE, "authorization": token, "deviceid": self.deviceid}

        await self._request(url, headers, payload)
        return True

    async def verify_device_code(self, token, email_enc, code):
        url = f"{BASE}/cma-app-user/api/login-device/email-verify"
        payload = {
            "authCode": code,
            "email": email_enc,
            "deviceName": DEVICE_NAME,
            "lastLoginTime": now_ts(),
            "type": "3",
            "deviceModel": DEVICE_NAME
        }
        headers = {**HEADERS_BASE, "authorization": token, "deviceid": self.deviceid}

        result = await self._request(url, headers, payload)
        if result.get("data") is not True:
            raise ValueError("Device verification was not confirmed")
        return True

    async def refresh_token(self):
        url = f"{BASE}/cma-app-auth/api/auth/refresh-token"
        headers = {**HEADERS_BASE, "authorization": self.token}

        body = {"refreshToken": self.refresh}

        async with self.session.post(url, headers=headers, json=body) as resp:
            raw = await resp.json()

        if not raw.get("success"):
            raise ConfigEntryAuthFailed("Token refresh failed")

        self.token = raw["data"]["token"]
        self.refresh = raw["data"]["refreshToken"]
        return self.token

    async def async_get_vehicles(self) -> list[Mazda6eVehicle]:
        headers = {
            **HEADERS_BASE,
            "authorization": self.token,
            "deviceid": self.deviceid,
        }

        try:
            raw = await self._request(
                f"{BASE}/cma-app-user/api/vehicle/vehicles",
                headers,
                {},
            )
        except Exception as err:
            _LOGGER.debug("Legacy vehicle endpoint unavailable: %s", err)
            raw = await self._request(
                f"{BASE}/cma-app-user/api/car/vehicles",
                headers,
                {},
            )

        vehicles = []
        for v in raw.get("data", []):
            vehicles.append(
                Mazda6eVehicle(
                    vehicle_id=v.get("carId") or v["vehicleId"],
                    vin=v["vin"],
                    model_name=v["modelName"],
                    car_name=v.get("carName"),
                    plate_number=v.get("plateNumber"),
                    series_name=v.get("seriesName"),
                )
            )
        return vehicles

    async def async_get_function_config(self, vehicle_id: int) -> set[str]:
        """Return the function codes the vehicle supports (e.g. '#findCar', 'ACSW')."""
        url = f"{BASE}/cma-app-user/api/vehicle/function-config"
        headers = {
            **HEADERS_BASE,
            "authorization": self.token,
            "deviceid": self.deviceid,
        }

        raw = await self._request(url, headers, {"vehicleId": vehicle_id})
        return set((raw.get("data") or {}).get("confList") or [])

    async def async_get_vehicle_status(self, vehicle_id: int):
        url = f"{BASE}/cma-app-car-condition/api/vehicle/condition/v2"
        headers = {
            **HEADERS_BASE,
            "authorization": self.token,
            "deviceid": self.deviceid,
        }

        body = {
            "vechileCriteria": {
                "seat": "1",
                "tire": "1",
                "charge": "1",
                "vehicleStatus": "1",
                "hvac": "1",
                "departurePlan": "0",
                "fuel": "0",
                "window": "1",
                "door": "1",
                "airConditionPlan": "0",
                "lamp": "1",
                "warmCoolingBox": "0",
                "welcome": "0",
                "location": "1"
            },
            "vehicleId": vehicle_id
        }

        raw = await self._request(url, headers, body)
        return raw.get("data")

    async def async_unlock(self, vehicle_id: int):
        """Unlock the vehicle doors through Mazda cloud control."""
        return await self._async_door_control(vehicle_id, open_doors=True)

    async def async_set_air_conditioner(
        self, vehicle_id: int, enabled: bool, target_temp: float, run_time: int = 15,
    ):
        """Set remote cabin climate using Mazda's signed cloud-control endpoint."""
        return await self._async_signed_control(
            vehicle_id,
            "air-conditioner",
            {
                "enabled": enabled,
                "targetTemp": int(round(target_temp * 10)),
                "runTime": run_time,
            },
            allow_already_satisfied=True,
        )

    async def async_find_vehicle(self, vehicle_id: int):
        """Trigger Mazda's captured flashing-and-honking find-vehicle command."""
        return await self.async_flash_honk(vehicle_id, action_type=1)

    async def async_flash_honk(self, vehicle_id: int, action_type: int):
        """Trigger a captured Mazda flashing-and-honking action."""
        return await self._async_signed_control(
            vehicle_id,
            "flashing-honking",
            {"type": action_type},
        )

    async def async_set_windows(self, vehicle_id: int, open_windows: bool):
        """Open or close all vehicle windows through cloud control."""
        return await self._async_protected_control(
            vehicle_id,
            "windows",
            {"open": open_windows},
        )

    async def async_set_trunk(self, vehicle_id: int, open_trunk: bool):
        """Open or close the trunk through cloud control."""
        return await self._async_protected_control(
            vehicle_id,
            "trunk",
            {"open": open_trunk},
        )

    async def async_set_defrost(self, vehicle_id: int, enabled: bool):
        """Enable or disable the captured remote front-defrost control."""
        return await self._async_signed_control(vehicle_id, "defrost", {"enabled": enabled})

    async def async_set_steering_wheel_heat(self, vehicle_id: int, enabled: bool):
        """Enable or disable the captured steering-wheel heat control."""
        return await self._async_signed_control(vehicle_id, "steering-wheel/heat", {"open": enabled})

    async def async_set_seat_mode(
        self, vehicle_id: int, control: str, position: str, enabled: bool, level: int,
    ):
        """Set a captured front-seat heat or ventilation mode."""
        if position not in ("master", "copilot"):
            raise ValueError("Unknown seat position")
        if level not in (1, 2, 3):
            raise ValueError("Seat level must be between 1 and 3")
        return await self._async_signed_control(
            vehicle_id,
            f"seats/{control}",
            {f"{position}Switch": int(enabled), f"{position}Level": level},
        )

    async def async_lock(self, vehicle_id: int):
        """Lock the vehicle doors through Mazda cloud control."""
        return await self._async_door_control(vehicle_id, open_doors=False)

    async def _async_door_control(self, vehicle_id: int, *, open_doors: bool):
        """Authorize, submit, and poll a signed door-control command."""
        if not self.control_private_key:
            raise ConfigEntryAuthFailed("Sign in again to register a control key")
        if not self.control_pin:
            raise ConfigEntryAuthFailed("Sign in again to register the control passcode")
        headers = {**HEADERS_BASE, "authorization": self.token, "deviceid": self.deviceid}

        checked = await self._request(
            f"{BASE}/cma-app-car-control/api/security-code/check-code", headers,
            {"safeCode": encrypt_credential(self.control_pin)},
        )
        checked_data = checked.get("data")
        if not isinstance(checked_data, dict) or not isinstance(checked_data.get("rcToken"), str):
            raise ValueError("Control passcode response omitted rcToken")
        rc_token = checked_data["rcToken"]

        serial_response = await self._request(
            f"{BASE}/cma-app-car-control/api/serial-no/get", headers, {"type": "1"},
        )
        encrypted_serial = serial_response.get("data")
        if not isinstance(encrypted_serial, str):
            raise ValueError("Serial response omitted data")
        serial_no = decrypt_control_serial(encrypted_serial, self.control_private_key)
        submitted = await self._async_submit_signed_control(
            headers,
            "doors",
            {
                "open": open_doors,
                "rcToken": rc_token,
                "seriralNo": serial_no,
                "vehicleId": str(vehicle_id),
            },
        )
        submitted_data = submitted.get("data")
        if not isinstance(submitted_data, dict) or not isinstance(submitted_data.get("commandId"), str):
            raise ValueError("Door response omitted commandId")
        return await self._async_wait_for_control_result(
            headers, vehicle_id, submitted_data["commandId"], allow_already_locked=not open_doors,
        )

    async def _async_signed_control(
        self, vehicle_id: int, control_name: str, payload: dict, *, allow_already_satisfied: bool = False,
    ):
        """Submit and poll a captured signed Mazda control command."""
        if not self.control_private_key:
            raise ConfigEntryAuthFailed("Sign in again to register a control key")

        headers = {**HEADERS_BASE, "authorization": self.token, "deviceid": self.deviceid}
        serial_response = await self._request(
            f"{BASE}/cma-app-car-control/api/serial-no/get", headers, {"type": "1"},
        )
        encrypted_serial = serial_response.get("data")
        if not isinstance(encrypted_serial, str):
            raise ValueError("Serial response omitted data")

        payload = {
            **payload,
            "seriralNo": decrypt_control_serial(encrypted_serial, self.control_private_key),
            "vehicleId": str(vehicle_id),
        }
        submitted = await self._async_submit_signed_control(headers, control_name, payload)
        submitted_data = submitted.get("data")
        if not isinstance(submitted_data, dict) or not isinstance(submitted_data.get("commandId"), str):
            raise ValueError(f"{control_name} response omitted commandId")

        return await self._async_wait_for_control_result(
            headers,
            vehicle_id,
            submitted_data["commandId"],
            allow_already_locked=allow_already_satisfied,
        )

    async def _async_protected_control(
        self, vehicle_id: int, control_name: str, payload: dict,
    ):
        """Submit a command that requires a freshly authorized control passcode."""
        if not self.control_private_key:
            raise ConfigEntryAuthFailed("Sign in again to register a control key")
        if not self.control_pin:
            raise ConfigEntryAuthFailed("Sign in again to register the control passcode")

        headers = {**HEADERS_BASE, "authorization": self.token, "deviceid": self.deviceid}
        checked = await self._request(
            f"{BASE}/cma-app-car-control/api/security-code/check-code", headers,
            {"safeCode": encrypt_credential(self.control_pin)},
        )
        checked_data = checked.get("data")
        if not isinstance(checked_data, dict) or not isinstance(checked_data.get("rcToken"), str):
            raise ValueError("Control passcode response omitted rcToken")

        serial_response = await self._request(
            f"{BASE}/cma-app-car-control/api/serial-no/get", headers, {"type": "1"},
        )
        encrypted_serial = serial_response.get("data")
        if not isinstance(encrypted_serial, str):
            raise ValueError("Serial response omitted data")

        submitted = await self._async_submit_signed_control(
            headers,
            control_name,
            {
                **payload,
                "rcToken": checked_data["rcToken"],
                "seriralNo": decrypt_control_serial(encrypted_serial, self.control_private_key),
                "vehicleId": str(vehicle_id),
            },
        )
        submitted_data = submitted.get("data")
        if not isinstance(submitted_data, dict) or not isinstance(submitted_data.get("commandId"), str):
            raise ValueError(f"{control_name} response omitted commandId")

        return await self._async_wait_for_control_result(
            headers, vehicle_id, submitted_data["commandId"], allow_already_locked=False,
        )

    async def _async_submit_signed_control(
        self, headers: dict, control_name: str, payload: dict, *, sign_omit_keys: set[str] | None = None,
    ):
        signed_payload = {
            **payload,
            "sign": sign_control_payload(
                payload, self.control_private_key, omit_keys=sign_omit_keys,
            ),
        }
        return await self._request(
            f"{BASE}/cma-app-car-control/api/control/{control_name}", headers, signed_payload,
        )

    async def _async_wait_for_control_result(
        self, headers: dict, vehicle_id: int, command_id: str, *, allow_already_locked: bool,
    ):
        """Poll a command until Mazda accepts or rejects it."""

        for _ in range(15):
            result = await self._request(
                f"{BASE}/cma-app-car-control/api/control/control-result", headers,
                {"commandId": command_id, "vehicleId": str(vehicle_id)},
            )
            data = result.get("data")
            if not isinstance(data, dict) or type(data.get("resultCode")) is not int:
                raise ValueError("Unknown door-control result")
            result_code = data["resultCode"]
            if result_code == 0 or (allow_already_locked and result_code == 1015):
                return data
            if result_code != -100:
                raise RuntimeError(f"Control failed with result code {result_code}")
            await asyncio.sleep(1)
        raise TimeoutError("Control remained in PROCESSING state")
