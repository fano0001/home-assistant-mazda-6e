import aiohttp
import logging
import time

from .models import Mazda6eVehicle

_LOGGER = logging.getLogger(__name__)

BASE = "https://cma-m.iov.changanauto.com.de/cma-app-gw"

HEADERS_BASE = {
    "content-type": "application/json",
    "devicetype": "iPhone",
    "apptype": "IOS",
    "appid": "cma",
    "accept": "*/*",
    "appversion": "V1.1.2",
    "accept-language": "de-DE;q=1.0",
    "user-agent": "overseas/1.1.2 (com.mazda.mazda6e)",
    "language": "de_DE",
}

def now_ts():
    """Return current UNIX timestamp as string."""
    return str(int(time.time()))


class Mazda6EApi:
    def __init__(self, session: aiohttp.ClientSession, token=None, refresh=None):
        self.session = session
        self.token = token
        self.refresh = refresh

    async def login_email_password(self, email_enc, password_enc, deviceid):
        url = f"{BASE}/cma-app-auth/api/login/email-pass-in/v2"

        payload = {
            "loginTime": now_ts(),
            "email": email_enc,
            "password": password_enc,
            "pubKey": "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCRYk7lZkHwHCJo8sSoKs5UuD/Jh9j7Pv5Lnoc6wNpVcvGj1LG+a6Kyn+OoRSa0NP24MWoLd0WE+zRYJH2RFNdiXHDdHqZYcxtTsvwyMaBjI6jsizdXrbFc3oBZY4LMfr7nV66/nQB1TP7UO7fYMti3/wfHfbFG0BCgCgWeuGeRXQIDAQAB"
        }

        headers = {**HEADERS_BASE, "deviceid": deviceid}

        async with self.session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()

            if not data.get("success"):
                raise Exception("Email/Password Login failed")

            self.token = data["data"]["token"]
            self.refresh = data["data"]["refreshToken"]

            return data["data"]

    async def send_device_login(self, token, email_enc, device_name, deviceid):
        url = f"{BASE}/cma-app-user/api/send-email/device-login/send"

        payload = {
            "email": email_enc,
            "deviceName": device_name,
            "loginTime": now_ts(),
            "type": "1"
        }

        headers = {
            **HEADERS_BASE,
            "authorization": token,
            "deviceid": deviceid,
        }

        async with self.session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            if not data.get("success"):
                raise Exception("Device login initiation failed")

            return True

    async def verify_device_code(self, token, email_enc, code, device_name, deviceid):
        url = f"{BASE}/cma-app-user/api/login-device/email-verify"

        payload = {
            "authCode": code,
            "email": email_enc,
            "deviceName": device_name,
            "lastLoginTime": now_ts(),
            "type": "3",
            "deviceModel": device_name
        }

        headers = {
            **HEADERS_BASE,
            "authorization": token,
            "deviceid": deviceid,
        }

        async with self.session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            if not data.get("success"):
                raise Exception("Verification code incorrect")

            return True

    async def refresh_token(self):
        """New endpoint to refresh tokens when expired."""
        url = f"{BASE}/cma-app-auth/api/login/refresh-token"

        headers = {
            **HEADERS_BASE,
            "authorization": self.refresh
        }

        async with self.session.post(url, headers=headers) as resp:
            data = await resp.json()

            if not data.get("success"):
                raise Exception("Token refresh failed")

            self.token = data["data"]["token"]
            return self.token

    async def async_get_vehicles(self, deviceid) -> list[Mazda6eVehicle]:
        url = f"{BASE}/cma-app-user/api/vehicle/vehicles"
        headers = {
            **HEADERS_BASE,
            "authorization": self.token,
            "deviceid": deviceid,
        }

        async with self.session.post(url, headers=headers, json={}) as resp:
            data = await resp.json()

            vehicles = []
            for v in data.get("data", []):
                vehicles.append(
                    Mazda6eVehicle(
                        vehicle_id=v["vehicleId"],
                        vin=v["vin"],
                        model_name=v["modelName"],
                    )
                )
            return vehicles

    async def async_get_vehicle_status(self, vehicle_id: int, deviceid):
        url = f"{BASE}/cma-app-car-condition/api/vehicle/condition/v2"
        headers = {
            **HEADERS_BASE,
            "authorization": self.token,
            "deviceid": deviceid,
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

        async with self.session.post(url, headers=headers, json=body) as resp:
            data = await resp.json()
            return data.get("data")