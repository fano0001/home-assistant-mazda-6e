"""Asia MQTT status tests."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import AsyncMock

import pytest


ROOT = Path(__file__).parents[1] / "custom_components" / "mazda_6e"


def load_module(monkeypatch, name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules(monkeypatch):
    package = ModuleType("asia_test")
    package.__path__ = [str(ROOT)]
    exceptions = ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    monkeypatch.setitem(sys.modules, "asia_test", package)
    monkeypatch.setitem(sys.modules, "homeassistant", ModuleType("homeassistant"))
    monkeypatch.setitem(sys.modules, "homeassistant.exceptions", exceptions)
    const = load_module(monkeypatch, "asia_test.const", "const.py")
    load_module(monkeypatch, "asia_test.credential_crypto", "credential_crypto.py")
    load_module(monkeypatch, "asia_test.models", "models.py")
    mqtt = load_module(monkeypatch, "asia_test.mqtt", "mqtt.py")
    api = load_module(monkeypatch, "asia_test.api", "api.py")
    return api, const, mqtt


def connection_config():
    return {
        "mqttConnectionInfos": [{
            "clusterInfos": [{"brokerUrl": "ssl://broker.example", "brokerPort": "8883"}],
            "topicInfos": [
                {
                    "msgType": "loginout",
                    "pubTopics": ["$vdp/login-device/loginout/req"],
                    "subTopics": ["$vdp/login-device/loginout/res"],
                },
                {
                    "msgType": "properties",
                    "pubTopics": [
                        "$vdp/device-one/properties/set/req",
                        "$vdp/device-one/properties/get/req",
                        "$vdp/device-two/properties/get/req",
                    ],
                    "subTopics": [
                        "$vdp/device-one/properties/get/res",
                        "$vdp/device-two/properties/get/res",
                    ],
                },
                {
                    "msgType": "commands",
                    "pubTopics": ["$vdp/device-one/commands/req"],
                    "subTopics": ["$vdp/device-one/commands/res"],
                },
            ],
        }],
    }


def test_topic_groups_keep_both_read_topics(modules):
    _, _, mqtt = modules
    login_pub, login_sub, properties = mqtt._topic_groups(connection_config())
    assert login_pub.endswith("/loginout/req")
    assert login_sub.endswith("/loginout/res")
    assert properties == [
        ("$vdp/device-one/properties/get/req", "$vdp/device-one/properties/get/res"),
        ("$vdp/device-two/properties/get/req", "$vdp/device-two/properties/get/res"),
    ]


def test_topic_groups_exclude_write_and_command_topics(modules):
    _, _, mqtt = modules
    _, _, properties = mqtt._topic_groups(connection_config())
    flattened = " ".join(topic for pair in properties for topic in pair)
    assert "/set/" not in flattened
    assert "/commands/" not in flattened


def test_exchange_tries_second_properties_topic(modules, monkeypatch):
    _, _, mqtt = modules

    class Writer:
        def __init__(self):
            self.writes = []

        def write(self, value):
            self.writes.append(value)

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    writer = Writer()

    async def open_connection(*args, **kwargs):
        return object(), writer

    packets = iter([
        (0x20, b"\x00\x00"),  # CONNACK
        (0x90, b""),          # SUBACK
        (0x30, b"login"),
        (0x30, b"first"),
        (0x30, b"second"),
    ])

    async def read_packet(reader):
        return next(packets)

    payloads = iter([
        ("$vdp/login-device/loginout/res", {"rs": []}, None),
        ("$vdp/device-one/properties/get/res", {"r": "first"}, None),
        ("$vdp/device-two/properties/get/res", {"r": "second"}, None),
    ])

    monkeypatch.setattr(mqtt.asyncio, "open_connection", open_connection)
    monkeypatch.setattr(mqtt, "_read_packet", read_packet)
    monkeypatch.setattr(mqtt, "_parse_publish", lambda first, body: next(payloads))
    monkeypatch.setattr(mqtt, "_secret", lambda payload: "0123456789abcdef")
    results = iter([{}, {"soc": 72}])
    monkeypatch.setattr(mqtt, "_params", lambda payload, key: next(results))

    status = asyncio.run(mqtt._exchange(connection_config(), "password"))

    written = b"".join(writer.writes)
    assert b"device-one/properties/get/req" in written
    assert b"device-two/properties/get/req" in written
    assert status["vehicleStatus"]["soc"] == 72


def test_crypto_round_trip(modules):
    _, _, mqtt = modules
    services = [{"service_code": "car_condition", "params": {"soc": 72}}]
    encrypted = mqtt._encrypt_services(services, "0123456789abcdef", "request-id")
    assert mqtt._decrypt_services(encrypted, "0123456789abcdef", "request-id") == services


def test_normalize_verified_fields(modules):
    _, _, mqtt = modules
    status = mqtt.normalize_status({
        "socDsp": "72", "remainedPowerMile": "310", "totalOdometer": "1200.5",
        "driverDoor": 1, "passengerDoor": 0, "leftRearDoor": 0, "rightRearDoor": 1,
        "diverWindow": 0, "passengerWindow": 1, "leftRearWindow": 0, "rightRearWindow": 0,
        "vehicleTemperature": "23.5", "airConditioningSetTemperature": 21,
        "airStatus": 1, "BattACChrgInCurr": "6.5", "chargDeltMins": 45,
        "lastUpdatedAt": 123456,
    })
    assert status["vehicleStatus"] == {"soc": 72, "drvMileage": 310, "totalMileage": 1200.5}
    assert status["door"]["doors"] == [1, 0, 0, 1]
    assert status["window"]["windows"] == [0, 1, 0, 0]
    assert status["hvac"] == {"insideTemp": 235, "remoteTemp": 210, "acStatus": 1}
    assert status["charge"]["acChargeCurrent"] == 6.5
    assert status["charge"]["remainChargeTime"] == 45
    assert status["lastUpdatedAt"] == 123456


def test_normalize_does_not_guess_enums(modules):
    _, _, mqtt = modules
    status = mqtt.normalize_status({"ChrgSts": 6, "driverDoorLock": 1})
    assert "chargeStatus" not in status["charge"]
    assert "driverLock" not in status["door"]


def test_charge_time_sentinel_is_unknown(modules):
    _, _, mqtt = modules
    assert mqtt.normalize_status({"chargDeltMins": 8191})["charge"]["remainChargeTime"] is None


def test_asia_uses_bootstrap_and_mqtt(modules, monkeypatch):
    api_module, const, mqtt = modules
    client = api_module.Mazda6EApi(
        None, token="first|tsp-token", deviceid="12345678-1234-1234-1234-123456789abc",
        region=const.REGION_ASIA,
    )
    client._request = AsyncMock(side_effect=[
        {"success": True, "data": connection_config()},
        {"success": True, "data": {"authToken": "mqtt-password"}},
    ])
    read_status = AsyncMock(return_value={"vehicleStatus": {"soc": 72}})
    monkeypatch.setattr(api_module, "async_read_status", read_status)

    result = asyncio.run(client.async_get_vehicle_status("car-id"))

    assert result == {"vehicleStatus": {"soc": 72}}
    assert client._request.await_args_list[0].args[0].endswith("/api/device/getConnConf")
    assert client._request.await_args_list[0].args[2] == {
        "tuid": "", "carId": "car-id", "deviceId": "12345678123412341234123456789abc",
        "deviceType": "1",
    }
    assert client._request.await_args_list[1].args[2] == {}
    assert client._request.await_args_list[0].args[1]["X-Tsp-User-Token"] == "tsp-token"
    read_status.assert_awaited_once_with(connection_config(), "mqtt-password")


def test_europe_keeps_rest_status(modules):
    api_module, const, _ = modules
    client = api_module.Mazda6EApi(None, token="token", deviceid="device", region=const.REGION_EUROPE)
    client._request = AsyncMock(return_value={"success": True, "data": {"vehicleStatus": {}}})
    asyncio.run(client.async_get_vehicle_status(1))
    assert client._request.await_args.args[0].endswith("/cma-app-car-condition/api/vehicle/condition/v2")


def test_asia_uses_ca_function_config(modules):
    api_module, const, _ = modules
    client = api_module.Mazda6EApi(
        None, token="first|tsp-token", deviceid="12345678123412341234123456789abc",
        region=const.REGION_ASIA,
    )
    client._request = AsyncMock(return_value={"success": True, "data": [{
        "applicationFuncCode": "root",
        "children": [{"applicationFuncCode": "ACSW", "serviceCode": "air"}],
    }]})
    result = asyncio.run(client.async_get_function_config("car-id"))
    assert result == {"root", "ACSW", "air"}
    assert client._request.await_args.args[0].endswith("/api/device/appGetCarConfFunc")
    assert client._request.await_args.args[2] == {"carId": "car-id"}


def test_refresh_updates_persisted_token_pair(modules):
    api_module, const, _ = modules
    updated = []
    client = api_module.Mazda6EApi(
        None, token="old", refresh="old-refresh", deviceid="device", region=const.REGION_EUROPE,
        token_update_callback=lambda token, refresh: updated.append((token, refresh)),
    )

    class Response:
        status = 200

        async def json(self):
            return {"success": True, "data": {"token": "new", "refreshToken": "new-refresh"}}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    client.session = Session()
    asyncio.run(client.refresh_token())
    assert updated == [("new", "new-refresh")]


def test_http_error_is_not_decoded_as_json(modules):
    api_module, const, _ = modules

    class Response:
        status = 404

        async def json(self):
            raise AssertionError("JSON decoding must not run for HTTP errors")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    client = api_module.Mazda6EApi(
        Session(), token="token", deviceid="device", region=const.REGION_EUROPE,
    )
    with pytest.raises(api_module.MazdaApiError, match="HTTP 404"):
        asyncio.run(client._request("https://example.invalid", {}, {}))
