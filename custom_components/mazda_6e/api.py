import aiohttp
import time
import logging

from .const import PUB_KEY, DEVICE_NAME
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
    "appversion": "V1.1.3",
    "accept-language": "de-DE;q=1.0",
    "user-agent": "overseas/1.1.3 (com.mazda.mazda6e)",
    "language": "de_DE",
}


def now_ts():
    return str(int(time.time()))


class Mazda6EApi:
    def __init__(self, session: aiohttp.ClientSession, token=None, refresh=None, deviceid=None):
        self.session = session
        self.token = token
        self.refresh = refresh
        self.deviceid = deviceid

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

            headers = {**headers, "authorization": self.token}

            # try again once
            return await self._request(url, headers, body, retry=False)
        raise Exception(f"Mazda API error: {raw}")

    async def login_email_password(self, email_enc, password_enc):
        url = f"{BASE}/cma-app-auth/api/login/email-pass-in/v2"
        payload = {
            "loginTime": now_ts(),
            "email": email_enc,
            "password": password_enc,
            "pubKey": PUB_KEY
        }
        headers = {**HEADERS_BASE, "deviceid": self.deviceid}

        async with self.session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()

            if not data.get("success"):
                raise Exception("Email/Password Login failed")

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

        await self._request(url, headers, payload)
        return True

    async def refresh_token(self):
        url = f"{BASE}/cma-app-auth/api/auth/refresh-token"
        headers = {**HEADERS_BASE, "authorization": self.token}

        body = {"refreshToken": self.refresh}

        async with self.session.post(url, headers=headers, json=body) as resp:
            raw = await resp.json()

        _LOGGER.debug("refresh-token response: %s", raw)

        if not raw.get("success"):
            raise ConfigEntryAuthFailed("Token refresh failed")

        self.token = raw["data"]["token"]
        self.refresh = raw["data"]["refreshToken"]
        return self.token

    async def async_get_vehicles(self) -> list[Mazda6eVehicle]:
        url = f"{BASE}/cma-app-user/api/vehicle/vehicles"
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
                "location": "0"
            },
            "vehicleId": vehicle_id
        }

        raw = await self._request(url, headers, body)
        return raw.get("data")
