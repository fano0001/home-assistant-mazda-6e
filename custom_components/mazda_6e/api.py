import asyncio
import aiohttp
import time
import logging

from .const import DEVICE_NAME, REGION_EUROPE, REGION_ASIA, BASE_EU, BASE_ASIA
from .credential_crypto import decrypt_control_serial, encrypt_credential, sign_door_control
from .models import Mazda6eVehicle
from homeassistant.exceptions import ConfigEntryAuthFailed

_LOGGER = logging.getLogger(__name__)

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


def now_ts():
    return str(int(time.time()))


def base_url(region):
    if region == REGION_EUROPE:
        return BASE_EU
    if region == REGION_ASIA:
        return BASE_ASIA
    raise ValueError(f"Unsupported region: {region}")


class Mazda6EApi:
    def __init__(self, session: aiohttp.ClientSession, token=None, refresh=None,
                 deviceid=None, control_public_key=None, control_private_key=None,
                 control_pin=None, region=None):
        self.session = session
        self.token = token
        self.refresh = refresh
        self.deviceid = deviceid
        self.control_public_key = control_public_key
        self.control_private_key = control_private_key
        self.control_pin = control_pin
        self.region = region

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
        raise Exception("Mazda API request rejected")

    async def login_email_password(self, email_enc, password_enc):
        if not self.control_public_key:
            raise ValueError("Missing control public key")
        url = f"{base_url(self.region)}/cma-app-auth/api/login/email-pass-in/v2"
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
        url = f"{base_url(self.region)}/cma-app-user/api/send-email/device-login/send"
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
        url = f"{base_url(self.region)}/cma-app-user/api/login-device/email-verify"
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
        url = f"{base_url(self.region)}/cma-app-auth/api/auth/refresh-token"
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
        url = f"{base_url(self.region)}/cma-app-user/api/vehicle/vehicles"
        headers = {
            **HEADERS_BASE,
            "authorization": self.token,
            "deviceid": self.deviceid,
        }

        raw = await self._request(url, headers, {})

        vehicles = []
        for v in raw.get("data", []):
            vehicles.append(
                Mazda6eVehicle(
                    vehicle_id=v["vehicleId"],
                    vin=v["vin"],
                    model_name=v["modelName"],
                )
            )
        return vehicles

    async def async_get_vehicle_status(self, vehicle_id: int):
        url = f"{base_url(self.region)}/cma-app-car-condition/api/vehicle/condition/v2"
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
                "location": "0"
            },
            "vehicleId": vehicle_id
        }

        raw = await self._request(url, headers, body)
        return raw.get("data")

    async def async_unlock(self, vehicle_id: int):
        """Unlock the vehicle doors through Mazda cloud control."""
        return await self._async_door_control(vehicle_id, open_doors=True)

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
            f"{base_url(self.region)}/cma-app-car-control/api/security-code/check-code", headers,
            {"safeCode": encrypt_credential(self.control_pin)},
        )
        checked_data = checked.get("data")
        if not isinstance(checked_data, dict) or not isinstance(checked_data.get("rcToken"), str):
            raise ValueError("Control passcode response omitted rcToken")
        rc_token = checked_data["rcToken"]

        serial_response = await self._request(
            f"{base_url(self.region)}/cma-app-car-control/api/serial-no/get", headers, {"type": "1"},
        )
        encrypted_serial = serial_response.get("data")
        if not isinstance(encrypted_serial, str):
            raise ValueError("Serial response omitted data")
        serial_no = decrypt_control_serial(encrypted_serial, self.control_private_key)
        signature = sign_door_control(
            open_doors, rc_token, serial_no, vehicle_id, self.control_private_key,
        )
        submitted = await self._request(
            f"{base_url(self.region)}/cma-app-car-control/api/control/doors", headers,
            {"command": "lock", "open": open_doors, "rcToken": rc_token,
             "seriralNo": serial_no, "sign": signature, "vehicleId": str(vehicle_id)},
        )
        submitted_data = submitted.get("data")
        if not isinstance(submitted_data, dict) or not isinstance(submitted_data.get("commandId"), str):
            raise ValueError("Door response omitted commandId")
        command_id = submitted_data["commandId"]

        for _ in range(15):
            result = await self._request(
                f"{base_url(self.region)}/cma-app-car-control/api/control/control-result", headers,
                {"commandId": command_id, "vehicleId": str(vehicle_id)},
            )
            data = result.get("data")
            if not isinstance(data, dict) or type(data.get("resultCode")) is not int:
                raise ValueError("Unknown door-control result")
            result_code = data["resultCode"]
            if result_code == 0 or (not open_doors and result_code == 1015):
                return data
            if result_code != -100:
                raise RuntimeError(f"Door control failed with result code {result_code}")
            await asyncio.sleep(1)
        raise TimeoutError("Door control remained in PROCESSING state")
