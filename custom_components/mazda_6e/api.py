import asyncio
import aiohttp
import time
import logging
import re
from contextlib import suppress
from copy import deepcopy
from collections.abc import Callable

from .const import DEVICE_NAME, REGION_EUROPE, REGION_ASIA, BASE_EU, BASE_ASIA
from .credential_crypto import decrypt_control_serial, encrypt_credential, sign_control_payload
from .models import Mazda6eVehicle
from .mqtt import (MazdaMqttConnection, MazdaMqttError, _broker, _topic_groups,
                   async_read_status, notification_topics)
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


class MazdaApiError(Exception):
    """Mazda rejected an authenticated API request."""

    def __init__(self, message, *, code=None):
        super().__init__(message)
        value = str(code) if code is not None else ""
        self.code = value if re.fullmatch(r"[A-Z0-9_]{1,64}", value) else None


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
                 control_pin=None, region=None,
                 token_update_callback: Callable[[str, str], None] | None = None):
        self.session = session
        self.token = token
        self.refresh = refresh
        self.deviceid = deviceid
        self.control_public_key = control_public_key
        self.control_private_key = control_private_key
        self.control_pin = control_pin
        self.region = region
        self.token_update_callback = token_update_callback
        self._mqtt_lock = asyncio.Lock()
        self._mqtt_connections = {}

    async def _request(self, url: str, headers: dict, body: dict, retry: bool = True):
        """generic request method with token refresh handling"""
        async with self.session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                raise MazdaApiError(f"Mazda API request failed with HTTP {resp.status}", code=f"HTTP_{resp.status}")
            try:
                raw = await resp.json()
            except (aiohttp.ContentTypeError, ValueError):
                raise MazdaApiError("Mazda API returned an invalid response") from None

        if not isinstance(raw, dict):
            raise MazdaApiError("Mazda API returned an invalid response")

        if raw.get("success") is True:
            return raw

        # token expired
        if raw.get("code") == "APP_1_1_02_004":
            if not retry:
                raise ConfigEntryAuthFailed("Token expired and refresh failed")

            _LOGGER.debug("Token expired -> refreshing token...")
            await self.refresh_token()

            headers["authorization"] = self.token
            if "X-Tsp-User-Token" in headers:
                headers["X-Tsp-User-Token"] = self._tsp_user_token()

            # try again once
            return await self._request(url, headers, body, retry=False)
        code = raw.get("code", "unknown")
        message = raw.get("msg", "unknown error")
        raise MazdaApiError(f"Mazda API request rejected ({code}: {message})", code=code)

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
            if resp.status != 200:
                raise ConfigEntryAuthFailed("Token refresh failed")
            try:
                raw = await resp.json()
            except (aiohttp.ContentTypeError, ValueError):
                raise ConfigEntryAuthFailed("Token refresh failed") from None

        if not raw.get("success"):
            raise ConfigEntryAuthFailed("Token refresh failed")

        self.token = raw["data"]["token"]
        self.refresh = raw["data"]["refreshToken"]
        if self.token_update_callback:
            self.token_update_callback(self.token, self.refresh)
        return self.token

    def _tsp_user_token(self) -> str:
        """Return the CA token segment used by the Asia bootstrap API."""
        if not isinstance(self.token, str) or not self.token:
            raise ConfigEntryAuthFailed("Missing access token")
        return self.token.rsplit("|", 1)[-1]

    def _ca_headers(self) -> dict[str, str]:
        return {
            **HEADERS_BASE,
            "authorization": self.token,
            "deviceid": self.deviceid,
            "X-Tsp-User-Token": self._tsp_user_token(),
        }

    async def async_get_vehicles(self) -> list[Mazda6eVehicle]:
        headers = {
            **HEADERS_BASE,
            "authorization": self.token,
            "deviceid": self.deviceid,
        }

        try:
            raw = await self._request(
                f"{base_url(self.region)}/cma-app-user/api/vehicle/vehicles",
                headers,
                {},
            )
        except Exception as err:
            _LOGGER.debug("Legacy vehicle endpoint unavailable: %s", err)
            raw = await self._request(
                f"{base_url(self.region)}/cma-app-user/api/car/vehicles",
                headers,
                {},
            )
        else:
            # Some backends accept the legacy route but return no vehicles,
            # including accounts whose car is visible in the official app.
            if raw.get("data") == []:
                try:
                    raw = await self._request(
                        f"{base_url(self.region)}/cma-app-user/api/car/vehicles",
                        headers,
                        {},
                    )
                except Exception as err:
                    _LOGGER.debug("Alternative vehicle endpoint unavailable: %s", err)

        vehicles = []
        for v in raw.get("data", []):
            vehicles.append(Mazda6eVehicle(
                    vehicle_id=v.get("carId") or v["vehicleId"],
                    vin=v["vin"],
                    model_name=v["modelName"],
                    car_name=v.get("carName"),
                    plate_number=v.get("plateNumber"),
                    series_name=v.get("seriesName"),
                ))
        return vehicles

    async def async_get_function_config(self, vehicle_id: int) -> set[str]:
        """Return the function codes the vehicle supports (e.g. '#findCar', 'ACSW')."""
        if self.region == REGION_ASIA:
            raw = await self._request(
                "https://cma-m.iov.changanauto.sg/user-apigw/"
                "vot-connect-conf-center/api/device/appGetCarConfFunc",
                self._ca_headers(),
                {"carId": str(vehicle_id)},
            )
            codes: set[str] = set()

            def collect(value) -> None:
                if isinstance(value, dict):
                    for key in ("applicationFuncCode", "serviceCode"):
                        code = value.get(key)
                        if isinstance(code, str) and code:
                            codes.add(code)
                    for child in value.get("children") or []:
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)

            collect(raw.get("data"))
            return codes

        url = f"{base_url(self.region)}/cma-app-user/api/vehicle/function-config"
        headers = {
            **HEADERS_BASE,
            "authorization": self.token,
            "deviceid": self.deviceid,
        }

        raw = await self._request(url, headers, {"vehicleId": vehicle_id})
        return set((raw.get("data") or {}).get("confList") or [])

    async def async_get_vehicle_status(self, vehicle_id: int):
        if self.region == REGION_ASIA:
            return await self._async_get_asia_vehicle_status(vehicle_id)

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
                "location": "1"
            },
            "vehicleId": vehicle_id
        }

        raw = await self._request(url, headers, body)
        return raw.get("data")

    async def _async_get_asia_vehicle_status(self, vehicle_id: int | str):
        # Serialize snapshots and connection setup: reusing Mazda's login ID
        # on a second connection would disconnect the persistent listener.
        async with self._mqtt_lock:
            active = self._mqtt_connections.get(vehicle_id)
            if active is not None:
                connection, config = active
                try:
                    return await connection.async_read_status(config)
                except (MazdaMqttError, TimeoutError, OSError) as err:
                    raise MazdaApiError("MQTT status read failed") from err
            config, mqtt_token = await self.async_get_mqtt_config(vehicle_id)
            try:
                return await async_read_status(config, mqtt_token)
            except MazdaMqttError as err:
                raise MazdaApiError(str(err)) from err

    async def async_get_mqtt_config(self, vehicle_id: int | str):
        """Obtain regional broker configuration and a fresh MQTT credential."""
        async with asyncio.timeout(30):
            return await self._async_get_mqtt_config(vehicle_id)

    async def _async_get_mqtt_config(self, vehicle_id: int | str):
        headers = self._ca_headers()
        ca_base = (
            "https://cma-m.iov.changanauto.sg" if self.region == REGION_ASIA
            else "https://ca-m.iov.changanauto.com.de"
        )
        device_id = str(self.deviceid or "").replace("-", "")
        if len(device_id) != 32 or any(char not in "0123456789abcdefABCDEF" for char in device_id):
            raise MazdaApiError("MQTT requires a 32-character device ID")

        connection_body = {
            "tuid": "", "carId": str(vehicle_id), "deviceId": device_id, "deviceType": "1",
        }
        if self.region == REGION_EUROPE:
            functions = await self._request(
                f"{ca_base}/user-apigw/vot-connect-conf-center/api/device/appGetCarConfFunc",
                headers, {"carId": str(vehicle_id)},
            )

            def cloud_device(value):
                if isinstance(value, dict):
                    # The verified European getConnConf exchange uses a THU
                    # device from the car's cloud function config, not the app UUID.
                    if (value.get("msgType") == "commands"
                            and value.get("serviceCode") == "THU_Service"
                            and isinstance(value.get("deviceId"), str)
                            and value["deviceId"]):
                        return value["deviceId"]
                    return cloud_device(value.get("children"))
                if isinstance(value, list):
                    for child in value:
                        if found := cloud_device(child):
                            return found
                return None

            european_device = cloud_device(functions.get("data"))
            if european_device is None:
                raise MazdaApiError("European MQTT cloud device is missing", code="MQTT_NO_CLOUD_DEVICE")
            connection_body = {"deviceType": "1", "deviceId": european_device}

        config_response = await self._request(
            f"{ca_base}/user-apigw/vot-connect-conf-center/api/device/getConnConf",
            headers,
            connection_body,
        )
        token_response = await self._request(
            f"{ca_base}/user-apigw/vot-connect-auth-center/api/auth/getAuthTokenByUserId",
            headers,
            {},
        )
        config = config_response.get("data")
        token_data = token_response.get("data")
        mqtt_token = token_data.get("authToken") if isinstance(token_data, dict) else None
        if not isinstance(config, dict) or not isinstance(mqtt_token, str) or not mqtt_token:
            raise MazdaApiError("MQTT bootstrap returned incomplete data")
        return config, mqtt_token

    async def async_listen_updates(self, vehicle_ids, on_update, on_ready):
        """Listen until disconnected; the coordinator owns reconnect/backoff.

        Vehicles sharing a broker/login identity share one physical connection.
        Individual read configurations and notification routing stay per vehicle.
        """
        connections = []
        waiters = []
        try:
            async with self._mqtt_lock:
                groups = {}
                for vehicle_id in vehicle_ids:
                    config, password = await self.async_get_mqtt_config(vehicle_id)
                    topics = notification_topics(config)
                    if not topics:
                        raise MazdaMqttError("MQTT notification topics are missing")
                    login_pub, _, _ = _topic_groups(config)
                    group_key = (*_broker(config), login_pub)
                    group = groups.setdefault(group_key, {
                        "config": deepcopy(config), "password": password,
                        "vehicles": {}, "topics": {},
                    })
                    group["vehicles"][vehicle_id] = config
                    group["password"] = password
                    for topic in topics:
                        group["topics"].setdefault(topic, set()).add(vehicle_id)
                    if len(group["vehicles"]) > 1:
                        group["config"]["mqttConnectionInfos"][0]["topicInfos"].extend(
                            config["mqttConnectionInfos"][0]["topicInfos"],
                        )

                for group in groups.values():
                    def notify(topic, patch, routing=group["topics"]):
                        for vehicle_id in routing.get(topic, ()):
                            on_update(vehicle_id, patch)

                    connection = MazdaMqttConnection(group["config"], group["password"], notify)
                    connections.append(connection)
                    await connection.async_connect()
                    for vehicle_id, config in group["vehicles"].items():
                        self._mqtt_connections[vehicle_id] = (connection, config)
                on_ready()
            waiters = [asyncio.create_task(c.async_wait_disconnected()) for c in connections]
            if not waiters:
                raise MazdaMqttError("No vehicles available for MQTT")
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            raise MazdaMqttError("MQTT notification connection ended")
        finally:
            # Clear mappings and close sessions while holding the same lock as
            # Asia reads, before a polling snapshot can open another connection.
            async with self._mqtt_lock:
                self._mqtt_connections.clear()
                for connection in connections:
                    await connection.async_close()
            for task in waiters:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task

    @property
    def mqtt_report_diagnostics(self) -> list[dict]:
        connections = dict.fromkeys(connection for connection, _ in self._mqtt_connections.values())
        return [
            {"connection": index, "subscriptions": connection.notification_diagnostics}
            for index, connection in enumerate(connections, 1)
        ]

    async def async_get_battery_preheating_plan(self, vehicle_id: int) -> dict | None:
        """Return the captured battery-preheating plan for a vehicle."""
        headers = {
            **HEADERS_BASE,
            "authorization": self.token,
            "deviceid": self.deviceid,
        }
        raw = await self._request(
            f"{base_url(self.region)}/cma-app-car-control/api/heating-plans/query/list",
            headers,
            {"vehicleId": str(vehicle_id)},
        )
        plans = raw.get("data")
        if not isinstance(plans, list):
            raise ValueError("Battery-preheating response omitted plan list")
        return next(
            (plan for plan in plans if isinstance(plan, dict) and plan.get("planType") == 0),
            None,
        )

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

    async def async_set_charge_limit(self, vehicle_id: int, charge_limit: int):
        """Set the vehicle target state of charge."""
        if type(charge_limit) is not int or not 60 <= charge_limit <= 100:
            raise ValueError("Charge limit must be a whole percentage from 60 to 100")
        if not self.control_private_key:
            raise ConfigEntryAuthFailed("Sign in again to register a control key")

        for attempt in range(2):
            try:
                return await self._async_set_charge_limit_once(vehicle_id, charge_limit)
            except RuntimeError as err:
                if str(err) != "Control failed with result code 1000":
                    raise
                status = await self.async_get_vehicle_status(vehicle_id)
                if (status or {}).get("charge", {}).get("maxSocPercent") == charge_limit:
                    return {"resultCode": 1000, "errorMsg": "Requested charge limit is active"}
                if attempt == 1:
                    raise

    async def async_update_battery_preheating(
        self,
        vehicle_id: int,
        plan_id: int | str,
        plan_type: int,
        end_data: str,
    ) -> dict:
        """Enable or update the captured battery-preheating plan."""
        if type(plan_type) is not int or not isinstance(end_data, str):
            raise ValueError("Invalid battery-preheating plan")
        return await self._async_battery_preheating_command(
            vehicle_id,
            "update-plan",
            "COMMAND_HEATING_PLANS_UPDATE",
            {
                "endData": end_data,
                "planId": str(plan_id),
                "planType": plan_type,
            },
        )

    async def async_disable_battery_preheating(
        self, vehicle_id: int, plan_id: int | str,
    ) -> dict:
        """Disable the captured battery-preheating plan."""
        return await self._async_battery_preheating_command(
            vehicle_id,
            "plan-availability",
            "COMMAND_HEATING_PLANS_AVAILABILITY",
            {"enabled": False, "planId": str(plan_id)},
        )

    async def _async_battery_preheating_command(
        self,
        vehicle_id: int,
        route: str,
        command: str,
        plan_payload: dict,
    ) -> dict:
        """Submit and poll a captured battery-preheating command."""
        if not self.control_private_key:
            raise ConfigEntryAuthFailed("Sign in again to register a control key")

        headers = {**HEADERS_BASE, "authorization": self.token, "deviceid": self.deviceid}
        serial_response = await self._request(
            f"{base_url(self.region)}/cma-app-car-control/api/serial-no/get",
            headers,
            {"type": "5"},
        )
        encrypted_serial = serial_response.get("data")
        if not isinstance(encrypted_serial, str):
            raise ValueError("Serial response omitted data")

        payload = {
            **plan_payload,
            "command": command,
            "rcToken": "",
            "seriralNo": decrypt_control_serial(encrypted_serial, self.control_private_key),
            "vehicleId": str(vehicle_id),
        }
        submitted = await self._request(
            f"{base_url(self.region)}/cma-app-car-control/api/heating-plans/{route}",
            headers,
            {
                **payload,
                "sign": sign_control_payload(
                    payload,
                    self.control_private_key,
                    omit_keys={"command", "rcToken"},
                ),
            },
        )
        submitted_data = submitted.get("data")
        if not isinstance(submitted_data, dict) or not isinstance(submitted_data.get("commandId"), str):
            raise ValueError("Battery-preheating response omitted commandId")
        return await self._async_wait_for_control_result(
            headers,
            vehicle_id,
            submitted_data["commandId"],
            allow_already_locked=False,
        )

    async def _async_set_charge_limit_once(self, vehicle_id: int, charge_limit: int):
        """Submit one charge-limit command and wait for its result."""
        headers = {**HEADERS_BASE, "authorization": self.token, "deviceid": self.deviceid}
        serial_response = await self._request(
            f"{base_url(self.region)}/cma-app-car-control/api/serial-no/get",
            headers,
            {"type": "2"},
        )
        encrypted_serial = serial_response.get("data")
        if not isinstance(encrypted_serial, str):
            raise ValueError("Serial response omitted data")

        payload = {
            "chargePercentageMax": charge_limit,
            "command": "charge_max",
            "rcToken": "",
            "seriralNo": decrypt_control_serial(encrypted_serial, self.control_private_key),
            "vehicleId": str(vehicle_id),
        }
        submitted = await self._request(
            f"{base_url(self.region)}/cma-app-car-control/api/charge/percentage",
            headers,
            {
                **payload,
                "sign": sign_control_payload(
                    payload,
                    self.control_private_key,
                    omit_keys={"command", "rcToken"},
                ),
            },
        )
        submitted_data = submitted.get("data")
        if not isinstance(submitted_data, dict) or not isinstance(submitted_data.get("commandId"), str):
            raise ValueError("Charge-limit response omitted commandId")

        return await self._async_wait_for_control_result(
            headers,
            vehicle_id,
            submitted_data["commandId"],
            allow_already_locked=False,
        )

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
            f"{base_url(self.region)}/cma-app-car-control/api/serial-no/get", headers, {"type": "1"},
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
            f"{base_url(self.region)}/cma-app-car-control/api/security-code/check-code", headers,
            {"safeCode": encrypt_credential(self.control_pin)},
        )
        checked_data = checked.get("data")
        if not isinstance(checked_data, dict) or not isinstance(checked_data.get("rcToken"), str):
            raise ValueError("Control passcode response omitted rcToken")

        serial_response = await self._request(
            f"{base_url(self.region)}/cma-app-car-control/api/serial-no/get", headers, {"type": "1"},
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
            f"{base_url(self.region)}/cma-app-car-control/api/control/{control_name}", headers, signed_payload,
        )

    async def _async_wait_for_control_result(
            self, headers: dict, vehicle_id: int, command_id: str, *, allow_already_locked: bool,
    ):
        """Poll a command until Mazda accepts or rejects it."""

        for _ in range(15):
            result = await self._request(
                f"{base_url(self.region)}/cma-app-car-control/api/control/control-result", headers,
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
