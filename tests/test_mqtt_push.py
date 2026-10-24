"""Persistent transport and partial MQTT state updates; no Mazda connections."""

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_asia_status import connection_config, load_module, modules


def push_config():
    config = connection_config()
    config["mqttConnectionInfos"][0]["topicInfos"][1]["subTopics"].extend([
        "$vdp/device-one/properties/report", "$vdp/device-two/properties/report",
    ])
    config["mqttConnectionInfos"][0]["topicInfos"].append({
        "msgType": "events", "subTopics": ["$vdp/device-one/events/up"],
    })
    return config


def test_partial_report_preserves_missing_fields_and_array_members(modules):
    _, _, mqtt = modules
    original = {
        "vehicleStatus": {"soc": 70, "drvMileage": 300},
        "door": {"doors": [0, 0, 1, 0], "driverLock": 0},
        "tire": {"leftFront": {"pressure": 250}},
    }
    patch = mqtt.status_patch({"soc": 0, "passengerDoor": 1, "ChrgSts": 6})
    result = mqtt.apply_status_patch(original, patch)
    assert result["vehicleStatus"] == {"soc": 0, "drvMileage": 300}
    assert result["door"] == {"doors": [0, 1, 1, 0], "driverLock": 0}
    assert result["tire"] == original["tire"]
    assert "charge" not in patch  # Unknown enums are not guessed.
    assert original["door"]["doors"] == [0, 0, 1, 0]
    assert mqtt.status_patch({"soc": None, "unknown": 1}) == {}


def test_old_report_does_not_overwrite_newer_snapshot(modules):
    _, _, mqtt = modules
    original = {"lastUpdatedAt": "200", "vehicleStatus": {"soc": 70}}
    result = mqtt.apply_status_patch(original, {"lastUpdatedAt": 100, "vehicleStatus": {"soc": 60}})
    assert result == original


def test_mqtt_measurements_match_https_status_shape_and_precision(modules):
    _, _, mqtt = modules
    patch = mqtt.status_patch({
        "chargeSoc": 85.0, "socDsp": 52,
        "lfTyrePressure": 241.9, "rfTyrePressure": 244.7,
        "lrTyrePressure": 254.3, "rrTyrePressure": 251.6,
        "innerHumidity": 78.5, "pm25": 0,
    })
    assert patch == {
        "vehicleStatus": {"soc": 52},
        "charge": {"maxSocPercent": 85},
        "tire": {
            "leftFront": {"pressure": 241}, "rightFront": {"pressure": 244},
            "leftBack": {"pressure": 254}, "rightBack": {"pressure": 251},
        },
        "hvac": {"insideHumidity": 78, "insidePm25": 0},
    }


def test_single_tire_report_preserves_other_tires_and_warning(modules):
    _, _, mqtt = modules
    original = {
        "tire": {
            "leftFront": {"pressure": 240, "status": 1},
            "rightBack": {"pressure": 250, "status": 0},
        },
        "charge": {"maxSocPercent": 80},
    }
    patch = mqtt.status_patch({"lfTyrePressure": "242.9", "chargeSoc": None})
    assert patch == {"tire": {"leftFront": {"pressure": 242}}}
    assert mqtt.apply_status_patch(original, patch) == {
        "tire": {
            "leftFront": {"pressure": 242, "status": 1},
            "rightBack": {"pressure": 250, "status": 0},
        },
        "charge": {"maxSocPercent": 80},
    }


def test_unverified_enums_and_invalid_measurements_preserve_https_state(modules):
    _, _, mqtt = modules
    assert mqtt.status_patch({
        "latestDate": "2026-10-18T13:00:00Z",
        "frontDefrostStatus": 3, "frontDefrosterSwitch": 3,
        "steeringWheelHeating": 3, "chargeStatus": 9, "driverDoorLock": 2,
        "lfTyrePressure": "NaN", "rfTyrePressure": None,
        "innerHumidity": "inf", "pm25": True, "chargeSoc": "invalid",
    }) == {}


def test_enum_diagnostics_are_bounded_numeric_and_do_not_apply_states(modules):
    _, _, mqtt = modules
    client = mqtt.MazdaMqttConnection(push_config(), "password", lambda *_: None)
    topic = "$vdp/device-one/properties/report"
    for index in range(40):
        values = {
            "frontDefrostStatus": 3, "steeringWheelHeating": "3",
            "driverDoorLock": "private-value", "chargeStatus": "NaN",
            "token": "secret", "unknown": "private-value",
            "latestDate": "2026-10-18T13:00:00Z",
        }
        client._record_report(topic, {"rs": "encrypted"}, values, mqtt.status_patch(values))
    stats = next(item for item in client.notification_diagnostics if item["reports"])
    assert stats["mapped_reports"] == 0
    assert len(stats["enum_reports"]) == 32
    assert stats["enum_reports"][-1]["values"] == {
        "frontDefrostStatus": 3, "steeringWheelHeating": 3,
    }
    assert stats["enum_reports"][-1]["lastUpdatedAt"] == 1792328400000
    assert "private-value" not in json.dumps(stats)
    assert "secret" not in json.dumps(stats)


@pytest.mark.parametrize(("values", "expected"), [
    ({"frontDefrostStatus": 1}, 0),
    ({"frontDefrostStatus": 2}, 1),
    ({"frontDefrosterSwitch": 0}, 0),
    ({"frontDefrosterSwitch": "1"}, 1),
    ({"frontDefrostStatus": 2, "frontDefrosterSwitch": 1}, 1),
    ({"frontDefrostStatus": 1, "frontDefrosterSwitch": 1}, None),
    ({"frontDefrostStatus": 0}, None),
    ({"frontDefrosterSwitch": 2}, None),
    ({"frontDefrosterSwitch": True}, None),
])
def test_defrost_translation(values, expected, modules):
    _, _, mqtt = modules
    assert mqtt.status_patch(values) == (
        {"hvac": {"defrostStatus": expected}} if expected is not None else {}
    )


def test_observed_enum_values_translate_to_https_values(modules):
    _, _, mqtt = modules
    assert mqtt.status_patch({
        "steeringWheelHeating": 1, "chargeStatus": 3,
        "driverDoorLock": 0, "passengerDoorLock": 0, "powerStatusFeedBack": 2,
    }) == {
        "vehicleStatus": {"steeringWheelHeater": 0, "powerStatus": 2},
        "charge": {"chargeStatus": 0},
        "door": {"driverLock": 0, "passengerLock": 0},
    }
    assert mqtt.status_patch({"powerStatusFeedBack": 0}) == {"vehicleStatus": {"powerStatus": 0}}
    assert mqtt.status_patch({"steeringWheelHeating": 2}) == {"vehicleStatus": {"steeringWheelHeater": 1}}
    assert mqtt.status_patch({
        "steeringWheelHeating": 3, "chargeStatus": 6,
        "driverDoorLock": 2, "passengerDoorLock": 2, "powerStatusFeedBack": 1,
    }) == {}


def test_observed_off_reports_clear_previous_https_states(modules):
    _, _, mqtt = modules
    original = {
        "vehicleStatus": {"status": 1},
        "lamp": {"lowBeam": 1, "highBeam": 1, "positionLamp": 1, "leftTurn": 1, "rightTurn": 1},
        "seat": {
            "leftFront": {"mode": 1, "heatStatus": 3, "ventStatus": 0},
            "rightFront": {"mode": 2, "heatStatus": 0, "ventStatus": 2},
            "leftBack": {"mode": 0, "heatStatus": 0, "ventStatus": 0},
        },
        "tire": {"leftFront": {"status": 1, "pressure": 240}},
        "charge": {"chargeConStatus": 1, "dcChargeGunConnectStatus": 1},
    }
    patch = mqtt.status_patch({
        "carStatus": 2, "lowBeam": 0, "highBeam": 0, "positionLamp": 0,
        "turnLndicatorLeft": 0, "turnLndicatorRight": 0,
        "driverSeatHeatStatus": 0, "driverSeatAirStatus": 0,
        "passengerSeatHeatStatus": 0, "passengerSeatAirStatus": 0,
        "leftBackSeatHeatStatus": 6, "leftBackSeatVentilateStatus": 6,
        "lfPressureWarning": 0,
        "acChargeGunConnectionState": 0, "dcDhargeGunConnectionState": 0,
    })
    result = mqtt.apply_status_patch(original, patch)
    assert result["vehicleStatus"]["status"] == 2
    assert result["lamp"] == dict.fromkeys(original["lamp"], 0)
    for position in ("leftFront", "rightFront"):
        assert result["seat"][position] == {"mode": 0, "heatStatus": 0, "ventStatus": 0}
    assert "leftBack" not in patch["seat"]
    assert result["tire"]["leftFront"] == {"status": 0, "pressure": 240}
    assert result["charge"] == {"chargeConStatus": 0, "dcChargeGunConnectStatus": 0}
    assert original["seat"]["leftFront"]["mode"] == 1


def test_incomplete_seat_or_plug_report_does_not_invent_combined_state(modules):
    _, _, mqtt = modules
    assert mqtt.status_patch({"driverSeatHeatStatus": 0}) == {
        "seat": {"leftFront": {"heatStatus": 0}},
    }
    assert mqtt.status_patch({"acChargeGunConnectionState": 0}) == {}
    assert mqtt.status_patch({"dcDhargeGunConnectionState": 0}) == {
        "charge": {"dcChargeGunConnectStatus": 0},
    }
    assert mqtt.status_patch({
        "driverSeatHeatStatus": 0, "driverSeatAirStatus": 3,
        "acChargeGunConnectionState": 0, "dcDhargeGunConnectionState": 1,
    }) == {"seat": {"leftFront": {"heatStatus": 0, "ventStatus": 3, "mode": 2}}}


def test_remaining_unverified_codes_do_not_replace_https_values(modules):
    _, _, mqtt = modules
    assert mqtt.status_patch({
        "carStatus": 3, "lowBeam": 1, "highBeam": 2, "positionLamp": True,
        "turnLndicatorLeft": 1, "turnLndicatorRight": 3,
        "driverSeatHeatStatus": 6, "passengerSeatHeatStatus": 4,
        "driverSeatAirStatus": 6, "passengerSeatAirStatus": 4,
        "lfPressureWarning": 1, "rfPressureWarning": 2,
        "lrPressureWarning": 3, "rrPressureWarning": 255,
        "acChargeGunConnectionState": 1, "dcDhargeGunConnectionState": 1,
        "totalCurrent": "NaN", "chargeCurrentVal": 0, "vehicleSpeed": 20,
    }) == {}


@pytest.mark.parametrize("encrypted", [False, True])
def test_report_envelope_supports_declared_data_and_params(modules, encrypted):
    _, _, mqtt = modules
    services = [
        {"service_code": "car_condition", "data": {"soc": 72}},
        {"service_code": "car_condition", "params": {"passengerDoor": 1}},
        {"success": False, "params": {"soc": 99}},
    ]
    key, request_id = "0123456789abcdef", "report-1"
    encoded = mqtt._encrypt_services(services, key, request_id) if encrypted else services
    assert mqtt._params({"r": request_id, "sers": encoded}, key) == {"soc": 72, "passengerDoor": 1}


class MemoryBroker:
    """A scripted MQTT peer exercising real packet encoding and parsing."""

    def __init__(self, mqtt, *, reject=False, respond_ping=True):
        self.mqtt = mqtt
        self.reader = asyncio.StreamReader()
        self.secret = "0123456789abcdef"
        self.closed = False
        self.reject = reject
        self.respond_ping = respond_ping
        self.requests = []
        self.pings = 0
        self.pubacks = []

    def send(self, packet):
        self.reader.feed_data(packet)

    def report(self, topic, values, *, retained=False, qos=0, request_id="report-1"):
        payload = {
            "r": request_id, "e": 1, "z": "gzip", "mt": "properties",
            "sers": self.mqtt._encrypt_services(
                [{"service_code": "car_condition", "params": values}], self.secret, request_id,
            ),
        }
        body = self.mqtt._mqtt_string(topic)
        if qos:
            body += b"\x00\x23"
        body += b"\x00" + json.dumps(payload).encode()
        self.send(bytes([0x30 | (qos << 1) | int(retained)]) + self.mqtt._remaining_length(len(body)) + body)

    def write(self, packet):
        first = packet[0]
        length, pos = self.mqtt._variable_integer(packet, 1)
        body = packet[pos:pos + length]
        if first == 0x10:
            self.send(b"\x20\x03\x00\x00\x00")
        elif first == 0x82:
            count, offset = 0, 3  # identifier + empty properties
            while offset < len(body):
                size = int.from_bytes(body[offset:offset + 2], "big")
                offset += 2 + size + 1
                count += 1
            reasons = bytes([0x87 if self.reject else 1]) * count
            ack = body[:2] + b"\x00" + reasons
            self.send(b"\x90" + self.mqtt._remaining_length(len(ack)) + ack)
        elif first == 0x30:
            topic, payload, _ = self.mqtt._parse_publish(first, body)
            self.requests.append(topic)
            if topic.endswith("/loginout/req"):
                reply = {"r": payload["r"], "rs": [{"params": {"secretKey": self.secret}}]}
                self.send(self.mqtt._publish_packet(topic.replace("/req", "/res"), reply))
            else:
                response_topic = topic.replace("/req", "/res")
                # The unrelated response must not satisfy the pending read.
                self.report(response_topic, {"soc": 99}, request_id="another-request")
                self.report(response_topic, {"soc": 72}, request_id=payload["r"])
        elif first == 0xC0:
            self.pings += 1
            if self.respond_ping:
                self.send(b"\xd0\x00")
        elif first == 0x40:
            self.pubacks.append(body[:2])

    async def drain(self):
        pass

    def close(self):
        self.closed = True
        self.reader.feed_eof()

    async def wait_closed(self):
        pass


def connect_to_broker(monkeypatch, mqtt, broker):
    async def open_connection(*args, **kwargs):
        return broker.reader, broker
    monkeypatch.setattr(mqtt.asyncio, "open_connection", open_connection)


@pytest.mark.parametrize("suffix", ["properties/report", "events/up"])
def test_persistent_reports_update_directly_and_snapshots_reuse_connection(modules, monkeypatch, suffix):
    _, _, mqtt = modules

    async def scenario():
        broker = MemoryBroker(mqtt)
        connect_to_broker(monkeypatch, mqtt, broker)
        updates = []
        received = asyncio.Event()

        def notification(topic, patch):
            updates.append((topic, patch))
            received.set()

        client = mqtt.MazdaMqttConnection(push_config(), "password", notification)
        try:
            await client.async_connect()
            snapshot = await client.async_read_status(push_config())
            assert snapshot["vehicleStatus"]["soc"] == 72
            reads_before = list(broker.requests)
            topic = "$vdp/device-one/" + suffix
            broker.report(topic, {"soc": 73}, retained=True)
            broker.report("$vdp/other-car/properties/report", {"soc": 99})
            broker.report(topic, {"unknown": "private-payload-value"})
            broker.report(topic, {"soc": 74, "passengerDoor": 1}, qos=1)
            await asyncio.wait_for(received.wait(), 1)
            assert len(updates) == 1
            assert updates[0][1] == {"vehicleStatus": {"soc": 74}, "door": {"doors": [None, 1, None, None]}}
            assert broker.pubacks == [b"\x00\x23"]
            assert broker.requests == reads_before  # No status read per report.
            stats = next(item for item in client.notification_diagnostics if item["kind"] == suffix and item["reports"])
            assert stats["kind"] == suffix
            assert stats["reports"] == 2
            assert stats["mapped_reports"] == 1
            assert stats["fields_seen"] == {"unknown": 1, "soc": 1, "passengerDoor": 1}
            assert stats["mapped_fields_seen"] == {"door.doors[1]": 1, "vehicleStatus.soc": 1}
            serialized = json.dumps(client.notification_diagnostics)
            assert "private-payload-value" not in serialized
            assert "device-one" not in serialized
            assert "password" not in serialized
            responses = [item for item in client.notification_diagnostics if item["kind"] == "properties/get/res"]
            assert sum(item["reports"] for item in responses) == 2
            assert client.connected
        finally:
            await client.async_close()
        assert broker.closed
        assert not client.connected

    asyncio.run(scenario())


def test_cancel_during_subscribe_closes_tls_connection(modules, monkeypatch):
    _, _, mqtt = modules

    async def scenario():
        subscribed = asyncio.Event()

        class SilentBroker(MemoryBroker):
            def write(self, packet):
                if packet[0] == 0x82:
                    subscribed.set()
                else:
                    super().write(packet)

        broker = SilentBroker(mqtt)
        connect_to_broker(monkeypatch, mqtt, broker)
        client = mqtt.MazdaMqttConnection(push_config(), "password", lambda *_: None)
        task = asyncio.create_task(client.async_connect())
        await asyncio.wait_for(subscribed.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert broker.closed
        assert not client.connected

    asyncio.run(scenario())


def test_disconnect_fails_pending_snapshot_without_leaking_reader(modules, monkeypatch):
    _, _, mqtt = modules

    async def scenario():
        reading = asyncio.Event()

        class DisconnectingBroker(MemoryBroker):
            def write(self, packet):
                if b"/properties/get/req" in packet:
                    reading.set()
                    self.send(b"\xe0\x02\x80\x00")
                else:
                    super().write(packet)

        broker = DisconnectingBroker(mqtt)
        connect_to_broker(monkeypatch, mqtt, broker)
        client = mqtt.MazdaMqttConnection(push_config(), "password", lambda *_: None)
        await client.async_connect()
        try:
            with pytest.raises(mqtt.MazdaMqttError):
                await asyncio.wait_for(client.async_read_status(push_config()), 1)
            assert reading.is_set()
            assert not client.connected
            assert not client._pending
        finally:
            await client.async_close()
        assert broker.closed

    asyncio.run(scenario())


def test_numeric_timestamp_strings_and_nonfinite_values(modules):
    _, _, mqtt = modules
    assert mqtt.status_patch({"lastUpdatedAt": "1792300000000", "soc": 72}) == {
        "lastUpdatedAt": 1792300000000, "vehicleStatus": {"soc": 72},
    }
    assert mqtt.status_patch({"soc": "NaN", "remainedPowerMile": "inf"}) == {}


def test_connection_report_does_not_refresh_vehicle_telemetry_timestamp(modules):
    _, _, mqtt = modules
    assert mqtt.status_patch({"latestDate": "2026-10-18T13:00:00Z", "connectStatus": 1}) == {}


def test_rejected_subscription_closes_transport(modules, monkeypatch):
    _, _, mqtt = modules

    async def scenario():
        broker = MemoryBroker(mqtt, reject=True)
        connect_to_broker(monkeypatch, mqtt, broker)
        client = mqtt.MazdaMqttConnection(push_config(), "password", lambda *_: None)
        with pytest.raises(mqtt.MazdaMqttError, match="subscriptions were rejected"):
            await client.async_connect()
        assert broker.closed
        assert not client.connected

    asyncio.run(scenario())


@pytest.mark.parametrize("respond_ping", [False, True])
def test_heartbeat_and_disconnect_detection(modules, monkeypatch, respond_ping):
    _, _, mqtt = modules

    async def scenario():
        broker = MemoryBroker(mqtt, respond_ping=respond_ping)
        connect_to_broker(monkeypatch, mqtt, broker)
        client = mqtt.MazdaMqttConnection(push_config(), "password", lambda *_: None)
        client.HEARTBEAT_INTERVAL = 0.01
        try:
            await client.async_connect()
            if respond_ping:
                await asyncio.sleep(0.055)
                assert client.connected
                assert broker.pings >= 2
            else:
                with pytest.raises(mqtt.MazdaMqttError):
                    await asyncio.wait_for(client.async_wait_disconnected(), 1)
        finally:
            await client.async_close()
        assert broker.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("region", ["europe", "asia"])
def test_regional_bootstrap_and_shared_identity_connection(modules, monkeypatch, region):
    api, _, _ = modules

    async def scenario():
        client = api.Mazda6EApi(
            None, token="first|tsp-token", deviceid="12345678123412341234123456789abc", region=region,
        )
        second = json.loads(json.dumps(push_config()).replace("device-one", "car-two-one").replace("device-two", "car-two-two"))
        configs = [push_config(), second]
        responses = []
        for config in configs:
            if region == "europe":
                responses.append({"data": [{"children": [{
                    "msgType": "commands", "serviceCode": "THU_Service", "deviceId": "car-cloud-unit",
                }]}]})
            responses.extend([
                {"data": config}, {"data": {"authToken": "mqtt-password"}},
            ])
        client._request = AsyncMock(side_effect=responses)
        connections = []
        ready = asyncio.Event()
        updates = []

        class Connection:
            def __init__(self, config, password, notify):
                connections.append(self)
                self.notify = notify
                self.config = config
                self.closed = False
                self.async_read_status = AsyncMock(return_value={"vehicleStatus": {"soc": 72}})
                self.notification_diagnostics = [{"reports": 0}]

            async def async_connect(self):
                pass

            async def async_wait_disconnected(self):
                await asyncio.Future()

            async def async_close(self):
                self.closed = True

        monkeypatch.setattr(api, "MazdaMqttConnection", Connection)
        task = asyncio.create_task(client.async_listen_updates([1, 2], lambda *args: updates.append(args), ready.set))
        try:
            await asyncio.wait_for(ready.wait(), 1)
            assert len(connections) == 1
            assert client.mqtt_report_diagnostics == [{"connection": 1, "subscriptions": [{"reports": 0}]}]
            connection = connections[0]
            connection.notify("$vdp/car-two-one/properties/report", {"vehicleStatus": {"soc": 73}})
            assert updates == [(2, {"vehicleStatus": {"soc": 73}})]
            expected_host = "changanauto.sg" if region == "asia" else "changanauto.com.de"
            assert all(expected_host in call.args[0] for call in client._request.await_args_list)
            if region == "europe":
                assert all(call.args[0].startswith("https://ca-m.iov.changanauto.com.de/") for call in client._request.await_args_list)
                assert client._request.await_args_list[1].args[2] == {
                    "deviceType": "1", "deviceId": "car-cloud-unit",
                }
            if region == "asia":
                result = await client.async_get_vehicle_status(2)
                assert result["vehicleStatus"]["soc"] == 72
                connection.async_read_status.assert_awaited_once_with(second)
                assert client._request.await_count == 4
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert all(c.closed for c in connections)
        assert client._mqtt_connections == {}

    asyncio.run(scenario())


@pytest.fixture
def coordinator_context(modules, monkeypatch):
    api, _, _ = modules

    class Coordinator:
        def __init__(self, hass, **kwargs):
            self.hass = hass
            self.config_entry = SimpleNamespace(
                pref_disable_polling=False,
                async_create_background_task=lambda hass, coro, name: hass.async_create_background_task(coro, name),
                async_create_task=lambda hass, coro, name: hass.async_create_task(coro, name),
            )
            self.data = None
            self.update_interval = kwargs["update_interval"]
            self.async_request_refresh = AsyncMock()

        def async_set_updated_data(self, data):
            self.data = data

        async def async_shutdown(self):
            pass

    helpers = ModuleType("homeassistant.helpers")
    update = ModuleType("homeassistant.helpers.update_coordinator")
    update.DataUpdateCoordinator = Coordinator
    events = ModuleType("homeassistant.helpers.event")

    def track(hass, callback, interval):
        timer = {"callback": callback, "interval": interval.total_seconds(), "removed": False}
        hass.timers.append(timer)
        def remove():
            timer["removed"] = True
        return remove

    events.async_track_time_interval = track
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.update_coordinator", update)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.event", events)
    coordinator = load_module(monkeypatch, "asia_test.coordinator", "coordinator.py")

    class Hass:
        def __init__(self):
            self.timers = []
            self.tasks = []

        def async_create_background_task(self, coro, name):
            task = asyncio.create_task(coro, name=name)
            self.tasks.append(task)
            return task

        async_create_task = async_create_background_task

    return coordinator, Hass


def vehicle_data(soc=70, updated=100):
    return {
        1: {"vehicle": "one", "status": {"lastUpdatedAt": updated, "vehicleStatus": {"soc": soc, "drvMileage": 300}}, "battery_preheating_plan": {"enabled": True}},
        2: {"vehicle": "two", "status": {"vehicleStatus": {"soc": 80}}},
    }


def test_coordinator_push_updates_only_affected_vehicle_without_fetching(coordinator_context):
    module, Hass = coordinator_context
    client = SimpleNamespace(async_get_vehicle_status=AsyncMock())
    coordinator = module.Mazda6eCoordinator(Hass(), None, client)
    coordinator.data = vehicle_data()
    coordinator._handle_mqtt_update(1, {"vehicleStatus": {"soc": 72}})
    assert coordinator.data[1]["status"]["vehicleStatus"] == {"soc": 72, "drvMileage": 300}
    assert coordinator.data[1]["battery_preheating_plan"] == {"enabled": True}
    assert coordinator.data[2]["status"]["vehicleStatus"]["soc"] == 80
    client.async_get_vehicle_status.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()
    assert coordinator.mqtt_diagnostics["reports_received"] == 1
    assert coordinator.mqtt_diagnostics["seconds_since_report"] >= 0


def test_reports_during_full_refresh_preserve_chronological_order(coordinator_context):
    module, Hass = coordinator_context

    async def scenario():
        coordinator = module.Mazda6eCoordinator(Hass(), None, None)
        coordinator.data = vehicle_data()

        async def fetch():
            coordinator._handle_mqtt_update(1, {"lastUpdatedAt": 150, "vehicleStatus": {"soc": 71}})
            coordinator._handle_mqtt_update(1, {"lastUpdatedAt": 300, "door": {"driverLock": 1}})
            # This full response is newer than the SOC report but older than
            # the door report. Only the latter should override it.
            return vehicle_data(soc=72, updated=200)

        coordinator._async_fetch_full_data = fetch
        result = await coordinator._async_update_data()
        assert result[1]["status"]["vehicleStatus"]["soc"] == 72
        assert result[1]["status"]["door"]["driverLock"] == 1
        assert result[1]["status"]["lastUpdatedAt"] == 300

    asyncio.run(scenario())


def test_older_full_refresh_preserves_mqtt_fields_received_before_request(coordinator_context):
    module, Hass = coordinator_context

    async def scenario():
        coordinator = module.Mazda6eCoordinator(Hass(), None, None)
        coordinator.data = vehicle_data()
        coordinator._handle_mqtt_update(1, {
            "lastUpdatedAt": 300, "hvac": {"defrostStatus": 1},
            "window": {"windows": [1, None, None, None]},
        })

        async def fetch():
            result = vehicle_data(soc=75, updated=200)
            result[1]["status"].update({
                "hvac": {"defrostStatus": 0, "insideTemp": 220},
                "window": {"windows": [0, 0, 0, 1]},
            })
            return result

        coordinator._async_fetch_full_data = fetch
        result = await coordinator._async_update_data()
        assert result[1]["status"]["hvac"] == {"defrostStatus": 1, "insideTemp": 220}
        assert result[1]["status"]["window"]["windows"] == [1, 0, 0, 1]
        assert result[1]["status"]["vehicleStatus"]["soc"] == 75
        assert result[1]["status"]["lastUpdatedAt"] == 300

    asyncio.run(scenario())


def test_reconciliation_expires_each_mqtt_field_at_snapshot_timestamp(modules):
    _, _, mqtt = modules
    updates = mqtt.MqttStatusUpdates()
    updates.record({"lastUpdatedAt": 200, "hvac": {"defrostStatus": 1}})
    updates.record({"lastUpdatedAt": 300, "charge": {"maxSocPercent": 85}})
    snapshot = {
        "lastUpdatedAt": "250", "hvac": {"defrostStatus": 0},
        "charge": {"maxSocPercent": 80},
    }
    assert updates.reconcile(snapshot) == {
        "lastUpdatedAt": 300, "hvac": {"defrostStatus": 0},
        "charge": {"maxSocPercent": 85},
    }
    newer = {"lastUpdatedAt": 300, "charge": {"maxSocPercent": 90}}
    assert updates.reconcile(newer) == newer
    assert updates.reconcile({"lastUpdatedAt": 100}) == {"lastUpdatedAt": 100}


def test_undated_change_retires_previous_dated_value_for_same_field(modules):
    _, _, mqtt = modules
    updates = mqtt.MqttStatusUpdates()
    updates.record({"lastUpdatedAt": 300, "hvac": {"defrostStatus": 1}})
    updates.record({"hvac": {"defrostStatus": 0}})
    snapshot = {"lastUpdatedAt": 200, "hvac": {"defrostStatus": 0}}
    assert updates.reconcile(snapshot) == snapshot
    updates.record({"lastUpdatedAt": 400, "hvac": {"defrostStatus": 1}})
    undated = {"hvac": {"defrostStatus": 0}}
    assert updates.reconcile(undated) == undated
    assert updates.reconcile(snapshot) == snapshot


def test_mixed_dated_and_undated_reports_during_refresh(coordinator_context):
    module, Hass = coordinator_context

    async def scenario():
        coordinator = module.Mazda6eCoordinator(Hass(), None, None)
        coordinator.data = vehicle_data()

        async def fetch():
            coordinator._handle_mqtt_update(1, {"hvac": {"defrostStatus": 0}})
            coordinator._handle_mqtt_update(1, {"lastUpdatedAt": 300, "hvac": {"defrostStatus": 1}})
            coordinator._handle_mqtt_update(1, {"lastUpdatedAt": 400, "vehicleStatus": {"soc": 75}})
            return vehicle_data(soc=70, updated=200)

        coordinator._async_fetch_full_data = fetch
        result = await coordinator._async_update_data()
        assert result[1]["status"]["hvac"]["defrostStatus"] == 1
        assert result[1]["status"]["vehicleStatus"]["soc"] == 75

    asyncio.run(scenario())


def test_reconciliation_drops_removed_vehicle_and_ignores_stale_report(coordinator_context):
    module, Hass = coordinator_context

    async def scenario():
        coordinator = module.Mazda6eCoordinator(Hass(), None, None)
        coordinator.data = vehicle_data(updated=300)
        coordinator._handle_mqtt_update(1, {"lastUpdatedAt": 200, "hvac": {"defrostStatus": 1}})
        assert 1 not in coordinator._mqtt_status_updates
        coordinator._handle_mqtt_update(2, {"lastUpdatedAt": 400, "vehicleStatus": {"soc": 85}})

        async def fetch():
            return {1: vehicle_data(updated=350)[1]}

        coordinator._async_fetch_full_data = fetch
        result = await coordinator._async_update_data()
        assert "hvac" not in result[1]["status"]
        assert coordinator._mqtt_status_updates == {}

    asyncio.run(scenario())


def test_timers_are_independent_of_reports_and_unload_cleans_up(coordinator_context):
    module, Hass = coordinator_context

    async def scenario():
        hass = Hass()
        cleanup = asyncio.Event()

        async def listen(ids, on_update, on_ready):
            try:
                on_ready()
                await asyncio.Future()
            finally:
                cleanup.set()

        coordinator = module.Mazda6eCoordinator(hass, None, SimpleNamespace(async_listen_updates=listen))
        coordinator.data = vehicle_data()
        coordinator._vehicle_ids = {1, 2}
        coordinator.async_start()
        try:
            for _ in range(5):
                await asyncio.sleep(0)
            assert coordinator._mqtt_ready
            assert coordinator.update_interval is None
            assert [timer["interval"] for timer in hass.timers] == [900, 60]
            coordinator.async_request_refresh.reset_mock()
            for soc in range(73, 83):
                coordinator._handle_mqtt_update(1, {"vehicleStatus": {"soc": soc}})
            coordinator.async_request_refresh.assert_not_awaited()
            await hass.timers[0]["callback"](None)
            assert coordinator.async_request_refresh.await_count == 1
            await hass.timers[1]["callback"](None)
            assert coordinator.async_request_refresh.await_count == 1
            coordinator._mqtt_ready = False
            await hass.timers[1]["callback"](None)
            assert coordinator.async_request_refresh.await_count == 2
        finally:
            await coordinator.async_stop()
        assert cleanup.is_set()
        assert all(timer["removed"] for timer in hass.timers)
        assert all(task.done() for task in hass.tasks)

    asyncio.run(scenario())


def test_connection_failure_retries_and_stops_cleanly(coordinator_context):
    module, Hass = coordinator_context

    async def scenario():
        attempts = 0
        retried = asyncio.Event()

        async def listen(*args):
            nonlocal attempts
            attempts += 1
            if attempts >= 2:
                retried.set()
            raise OSError("unavailable")

        coordinator = module.Mazda6eCoordinator(Hass(), None, SimpleNamespace(async_listen_updates=listen))
        coordinator.RECONNECT_INITIAL_DELAY = 0.001
        coordinator.async_start()
        try:
            await asyncio.wait_for(retried.wait(), 1)
            assert not coordinator._mqtt_ready
        finally:
            await coordinator.async_stop()

    asyncio.run(scenario())


def test_failed_full_refresh_uses_fallback_even_with_healthy_mqtt(coordinator_context):
    module, Hass = coordinator_context

    async def scenario():
        coordinator = module.Mazda6eCoordinator(Hass(), None, None)
        coordinator._mqtt_ready = True
        coordinator._async_fetch_full_data = AsyncMock(side_effect=OSError("unavailable"))
        with pytest.raises(OSError):
            await coordinator._async_update_data()
        await coordinator._async_fallback_refresh(None)
        coordinator.async_request_refresh.assert_awaited_once()
        coordinator._async_fetch_full_data = AsyncMock(return_value=vehicle_data())
        await coordinator._async_update_data()
        await coordinator._async_fallback_refresh(None)
        assert coordinator.async_request_refresh.await_count == 1

    asyncio.run(scenario())


def test_disable_polling_preserves_direct_push(coordinator_context):
    module, Hass = coordinator_context

    async def scenario():
        coordinator = module.Mazda6eCoordinator(Hass(), None, None)
        coordinator.config_entry.pref_disable_polling = True
        coordinator.data = vehicle_data()
        await coordinator._async_periodic_refresh(None)
        await coordinator._async_fallback_refresh(None)
        coordinator._handle_mqtt_ready()
        coordinator._handle_mqtt_update(1, {"vehicleStatus": {"soc": 75}})
        coordinator.async_request_refresh.assert_not_awaited()
        assert coordinator.data[1]["status"]["vehicleStatus"]["soc"] == 75
        assert not coordinator.mqtt_diagnostics["fallback_active"]
        assert not coordinator.mqtt_diagnostics["polling_enabled"]
        await coordinator.async_shutdown()

    asyncio.run(scenario())


def test_unload_cancels_inflight_timer_refresh(coordinator_context):
    module, Hass = coordinator_context

    async def scenario():
        coordinator = module.Mazda6eCoordinator(Hass(), None, None)
        entered = asyncio.Event()

        async def refresh():
            entered.set()
            await asyncio.Future()

        coordinator.async_request_refresh = refresh
        task = asyncio.create_task(coordinator._async_periodic_refresh(None))
        await asyncio.wait_for(entered.wait(), 1)
        await coordinator.async_shutdown()
        assert task.cancelled()
        assert not coordinator._scheduled_refresh_tasks

    asyncio.run(scenario())


@pytest.mark.parametrize("prefix,position", [("driver", "leftFront"), ("passenger", "rightFront")])
@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_front_seat_heat_levels(prefix, position, level, modules):
    _, _, mqtt = modules
    patch = mqtt.status_patch({f"{prefix}SeatHeatStatus": level, f"{prefix}SeatAirStatus": 0})
    assert patch == {"seat": {position: {"heatStatus": level, "ventStatus": 0, "mode": 1 if level else 0}}}


@pytest.mark.parametrize("prefix,position", [("driver", "leftFront"), ("passenger", "rightFront")])
@pytest.mark.parametrize("level", [1, 2, 3])
def test_front_seat_ventilation_levels(prefix, position, level, modules):
    _, _, mqtt = modules
    assert mqtt.status_patch({f"{prefix}SeatHeatStatus": 0, f"{prefix}SeatAirStatus": level}) == {
        "seat": {position: {"heatStatus": 0, "ventStatus": level, "mode": 2}},
    }


def test_partial_active_seat_and_conflicting_modes(modules):
    _, _, mqtt = modules
    assert mqtt.status_patch({"driverSeatHeatStatus": 2}) == {
        "seat": {"leftFront": {"heatStatus": 2, "mode": 1}},
    }
    assert mqtt.status_patch({"passengerSeatAirStatus": 3}) == {
        "seat": {"rightFront": {"ventStatus": 3, "mode": 2}},
    }
    assert mqtt.status_patch({"driverSeatHeatStatus": 2, "driverSeatAirStatus": 3}) == {
        "seat": {"leftFront": {"heatStatus": 2, "ventStatus": 3}},
    }


@pytest.mark.parametrize("prefix,position", [("leftBack", "leftBack"), ("rightBack", "rightBack")])
@pytest.mark.parametrize("level", [0, 1, 2, 3])
@pytest.mark.parametrize("control", ["heat", "vent"])
def test_rear_seat_levels(prefix, position, level, control, modules):
    _, _, mqtt = modules
    heat = level if control == "heat" else 0
    vent = level if control == "vent" else 0
    patch = mqtt.status_patch({f"{prefix}SeatHeatStatus": heat, f"{prefix}SeatVentilateStatus": vent})
    assert patch == {"seat": {position: {
        "heatStatus": heat, "ventStatus": vent,
        "mode": (1 if control == "heat" else 2) if level else 0,
    }}}


def test_unsupported_rear_seat_code_preserves_snapshot(modules):
    _, _, mqtt = modules
    original = {"seat": {"leftBack": {"heatStatus": 0, "ventStatus": 0, "mode": 0}}}
    patch = mqtt.status_patch({"leftBackSeatHeatStatus": 6, "leftBackSeatVentilateStatus": 6})
    assert patch == {}
    assert mqtt.apply_status_patch(original, patch) == original


@pytest.mark.parametrize("code", [0, 1])
def test_door_lock_codes(code, modules):
    _, _, mqtt = modules
    patch = mqtt.status_patch({"driverDoorLock": code, "passengerDoorLock": code})
    assert patch == {"door": {"driverLock": code, "passengerLock": code}}


@pytest.mark.parametrize("code", [1, 2])
def test_vehicle_status_codes(code, modules):
    _, _, mqtt = modules
    assert mqtt.status_patch({"carStatus": code}) == {"vehicleStatus": {"status": code}}


@pytest.mark.parametrize("current", [0, 0.5, 11.8, 22.4, 23.2])
def test_total_current_matches_dc_field_without_replacing_total_charge_current(current, modules):
    _, _, mqtt = modules
    original = {"charge": {"chargeCurrent": 0, "dcChargeCurrent": 19, "acChargeCurrent": 0}}
    patch = mqtt.status_patch({"totalCurrent": current})
    assert patch == {"charge": {"dcChargeCurrent": current}}
    assert mqtt.apply_status_patch(original, patch) == {
        "charge": {"chargeCurrent": 0, "dcChargeCurrent": current, "acChargeCurrent": 0},
    }


def test_explicit_dc_current_takes_priority_over_total_current(modules):
    _, _, mqtt = modules
    assert mqtt.status_patch({"BattDCChrgInCurr": 0, "totalCurrent": 19}) == {
        "charge": {"dcChargeCurrent": 0},
    }
