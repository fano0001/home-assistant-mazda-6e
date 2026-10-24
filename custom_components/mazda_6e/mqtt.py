"""Read-only MQTT 5 snapshots and persistent vehicle notifications."""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import math
import ssl
import struct
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from time import monotonic

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class MazdaMqttError(Exception):
    """Raised when a read-only MQTT status exchange fails."""


_ENUM_FIELDS = (
    "frontDefrostStatus", "frontDefrosterSwitch", "rearDefrosterSwitch",
    "steeringWheelHeating", "driverDoorLock", "passengerDoorLock",
    "leftRearDoorLock", "rightRearDoorLock", "chargeStatus",
    "batteryChargeStatus", "ChrgSts", "acChargeGunConnectionState",
    "dcDhargeGunConnectionState", "powerStatusFeedBack",
    "driverSeatHeatStatus", "passengerSeatHeatStatus",
    "driverSeatAirStatus", "passengerSeatAirStatus",
    "leftBackSeatHeatStatus", "rightBackSeatHeatStatus",
    "leftBackSeatVentilateStatus", "rightBackSeatVentilateStatus",
    "lowBeam", "highBeam", "positionLamp", "turnLndicatorLeft", "turnLndicatorRight",
    "lfPressureWarning", "rfPressureWarning", "lrPressureWarning", "rrPressureWarning",
    "carStatus", "vehicleSpeed", "chargeCurrentVal", "obcChrgInpAcI", "totalCurrent",
)


def _mqtt_string(value: str) -> bytes:
    encoded = value.encode()
    return struct.pack("!H", len(encoded)) + encoded


def _remaining_length(length: int) -> bytes:
    result = bytearray()
    while True:
        byte = length % 128
        length //= 128
        if length:
            byte |= 0x80
        result.append(byte)
        if not length:
            return bytes(result)


def _connect_packet(client_id: str, username: str, password: str) -> bytes:
    payload = _mqtt_string(client_id) + _mqtt_string(username) + _mqtt_string(password)
    variable = _mqtt_string("MQTT") + bytes((5, 0xC2)) + struct.pack("!H", 60) + b"\x00"
    body = variable + payload
    return b"\x10" + _remaining_length(len(body)) + body


def _subscribe_packet(packet_id: int, topics: list[str]) -> bytes:
    payload = b"".join(_mqtt_string(topic) + b"\x01" for topic in topics)
    body = struct.pack("!H", packet_id) + b"\x00" + payload
    return b"\x82" + _remaining_length(len(body)) + body


def _publish_packet(topic: str, payload: Mapping[str, Any]) -> bytes:
    body = _mqtt_string(topic) + b"\x00" + json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    return b"\x30" + _remaining_length(len(body)) + body


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    first = (await reader.readexactly(1))[0]
    multiplier = 1
    length = 0
    while True:
        byte = (await reader.readexactly(1))[0]
        length += (byte & 0x7f) * multiplier
        if not byte & 0x80:
            break
        multiplier *= 128
        if multiplier > 128**3:
            raise MazdaMqttError("Invalid MQTT packet length")
    if length > 4 * 1024 * 1024:
        raise MazdaMqttError("MQTT packet is too large")
    return first, await reader.readexactly(length)


def _variable_integer(data: bytes, pos: int) -> tuple[int, int]:
    multiplier = 1
    value = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value += (byte & 0x7f) * multiplier
        if not byte & 0x80:
            return value, pos
        multiplier *= 128
        if multiplier > 128**3:
            raise MazdaMqttError("Invalid MQTT property length")
    raise MazdaMqttError("Truncated MQTT property length")


def _parse_publish(first: int, body: bytes) -> tuple[str, dict[str, Any], int | None]:
    if len(body) < 2:
        raise MazdaMqttError("Invalid MQTT publish packet")
    topic_length = struct.unpack("!H", body[:2])[0]
    qos = (first >> 1) & 0x03
    if not topic_length or qos > 1 or 2 + topic_length >= len(body):
        raise MazdaMqttError("Invalid MQTT publish header")
    pos = 2
    topic = body[pos:pos + topic_length].decode()
    pos += topic_length
    packet_id = None
    if qos:
        if pos + 2 >= len(body):
            raise MazdaMqttError("Truncated MQTT packet identifier")
        packet_id = struct.unpack("!H", body[pos:pos + 2])[0]
        if packet_id == 0:
            raise MazdaMqttError("Invalid MQTT packet identifier")
        pos += 2
    property_length, pos = _variable_integer(body, pos)
    pos += property_length
    if pos >= len(body):
        raise MazdaMqttError("Truncated MQTT payload")
    value = json.loads(body[pos:].decode())
    if not isinstance(value, dict):
        raise MazdaMqttError("Invalid MQTT payload")
    return topic, value, packet_id


def _topic_device_id(topic: str) -> str | None:
    parts = topic.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "$vdp" else None


def _topic_groups(config: Mapping[str, Any]) -> tuple[str, str, list[tuple[str, str]]]:
    infos = config.get("mqttConnectionInfos")
    if not isinstance(infos, list) or not infos or not isinstance(infos[0], dict):
        raise MazdaMqttError("MQTT configuration is incomplete")
    topic_infos = infos[0].get("topicInfos")
    if not isinstance(topic_infos, list):
        raise MazdaMqttError("MQTT topics are missing")

    login_publish = login_subscribe = None
    properties: list[tuple[str, str]] = []
    for entry in topic_infos:
        if not isinstance(entry, dict):
            continue
        msg_type = str(entry.get("msgType", "")).lower()
        pubs = [item for item in entry.get("pubTopics") or [] if isinstance(item, str)]
        subs = [item for item in entry.get("subTopics") or [] if isinstance(item, str)]
        if msg_type == "loginout":
            login_publish = next((topic for topic in pubs if topic.endswith("/loginout/req")), None)
            login_subscribe = next((topic for topic in subs if topic.endswith("/loginout/res")), None)
        elif msg_type == "properties":
            requests = [topic for topic in pubs if topic.endswith("/properties/get/req")]
            responses = [topic for topic in subs if topic.endswith("/properties/get/res")]
            response_by_did = {_topic_device_id(topic): topic for topic in responses}
            for request in requests:
                response = response_by_did.get(_topic_device_id(request))
                if response and (request, response) not in properties:
                    properties.append((request, response))

    if not login_publish or not login_subscribe or not properties:
        raise MazdaMqttError("MQTT read topics are missing")
    return login_publish, login_subscribe, properties


def _broker(config: Mapping[str, Any]) -> tuple[str, int]:
    infos = config.get("mqttConnectionInfos")
    info = infos[0] if isinstance(infos, list) and infos else None
    clusters = info.get("clusterInfos") if isinstance(info, dict) else None
    cluster = clusters[0] if isinstance(clusters, list) and clusters else None
    if not isinstance(cluster, dict):
        raise MazdaMqttError("MQTT broker configuration is missing")
    host = str(cluster.get("brokerUrl") or "").removeprefix("ssl://")
    try:
        port = int(cluster.get("brokerPort") or 8883)
    except (TypeError, ValueError) as err:
        raise MazdaMqttError("Invalid MQTT broker port") from err
    if not host or port != 8883:
        raise MazdaMqttError("Invalid MQTT broker configuration")
    return host, port


def _request_id(device_id: str) -> str:
    return f"{device_id}_{datetime.now(UTC).timestamp() * 1_000_000:.0f}"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _login_payload(device_id: str, request_id: str) -> dict[str, Any]:
    return {
        "did": device_id, "r": request_id, "v": "v1.0.0", "mt": "loginout",
        "e": 0, "z": "unzip", "tf": 0, "dt": _now(),
        "sers": [{"service_code": "login", "params": {
            "encryptEnable": 1, "zipType": "gzip",
            "ts": int(datetime.now(UTC).timestamp() * 1000),
        }}],
        "b": {"ruid": device_id},
    }


def _decode_base64(value: str) -> bytes:
    return base64.b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def _encrypt_services(services: list[dict[str, Any]], key: str, request_id: str) -> str:
    compressed = base64.b64encode(gzip.compress(json.dumps(
        services, separators=(",", ":"), ensure_ascii=False,
    ).encode()))
    padder = padding.PKCS7(128).padder()
    padded = padder.update(compressed) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(key.encode()), modes.CBC(hashlib.md5(request_id.encode()).digest()),
    ).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def _decrypt_services(value: str, key: str, request_id: str) -> list[dict[str, Any]]:
    decryptor = Cipher(
        algorithms.AES(key.encode()), modes.CBC(hashlib.md5(request_id.encode()).digest()),
    ).decryptor()
    padded = decryptor.update(_decode_base64(value)) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    compressed = _decode_base64((unpadder.update(padded) + unpadder.finalize()).decode())
    decoded = json.loads(gzip.decompress(compressed).decode())
    return decoded if isinstance(decoded, list) else []


def _secret(payload: Mapping[str, Any]) -> str | None:
    for item in payload.get("rs") or []:
        if not isinstance(item, dict):
            continue
        for field in ("params", "data"):
            value = item.get(field)
            if isinstance(value, dict) and isinstance(value.get("secretKey"), str):
                return value["secretKey"]
    return None


def _condition_payload(device_id: str, login_id: str, key: str, request_id: str) -> dict[str, Any]:
    services = [{"service_code": "car_condition", "params": {"fetchPropertyType": 0}}]
    return {
        "did": device_id, "r": request_id, "v": "v1.0.0", "mt": "properties",
        "e": 1, "z": "gzip", "tf": 0, "dt": _now(), "rt": "",
        "b": {"ruid": login_id},
        "sers": _encrypt_services(services, key, request_id),
    }


def _params(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    request_id = payload.get("r")
    if not isinstance(request_id, str):
        return {}
    result: dict[str, Any] = {}
    for field in ("rs", "sers"):
        encoded = payload.get(field)
        if isinstance(encoded, list):
            services = encoded
        elif isinstance(encoded, str):
            try:
                services = _decrypt_services(encoded, key, request_id)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
        else:
            continue
        for service in services:
            if not isinstance(service, dict) or service.get("success") is False:
                continue
            # Both fields are declared by the app's MqttPropertiesCarConditionRes.
            for name in ("data", "params"):
                values = service.get(name)
                if isinstance(values, dict):
                    result.update(values)
    return result


def notification_topics(config: Mapping[str, Any]) -> list[str]:
    """Use only report/event subscriptions supplied by Mazda, never invented topics."""
    infos = config.get("mqttConnectionInfos") or []
    if not infos or not isinstance(infos[0], dict):
        return []
    return list(dict.fromkeys(
        topic
        for entry in infos[0].get("topicInfos") or []
        if isinstance(entry, dict) and entry.get("msgType") in ("properties", "events")
        for topic in entry.get("subTopics") or []
        if isinstance(topic, str) and topic.endswith(("/properties/report", "/events/up"))
    ))


def merge_status(current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Merge partial reports without clearing fields omitted from a report."""
    result = dict(current)
    for key, value in patch.items():
        old = result.get(key)
        if isinstance(value, dict):
            result[key] = merge_status(old if isinstance(old, dict) else {}, value)
        elif isinstance(value, list):
            previous = old if isinstance(old, list) else []
            result[key] = [
                value[index] if index < len(value) and value[index] is not None
                else previous[index] if index < len(previous) else None
                for index in range(max(len(value), len(previous)))
            ]
        elif value is not None:
            result[key] = value
    return result


def apply_status_patch(current: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Discard a report older than the current vehicle snapshot, when dated."""
    previous, updated = current.get("lastUpdatedAt"), patch.get("lastUpdatedAt")
    try:
        if previous is not None and updated is not None and float(updated) < float(previous):
            return dict(current)
    except (TypeError, ValueError):
        pass
    return merge_status(current, patch)


class MqttStatusUpdates:
    """Keep the latest dated MQTT value per field until a snapshot catches up."""

    def __init__(self):
        self._fields = {}

    def record(self, patch: Mapping[str, Any]) -> None:
        updated = _number(patch.get("lastUpdatedAt"))

        def walk(value, path=()):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key != "lastUpdatedAt":
                        walk(item, (*path, key))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, (*path, index))
            elif value is not None:
                if updated is None:
                    self._fields.pop(path, None)
                else:
                    previous = self._fields.get(path)
                    if previous is None or updated >= previous[0]:
                        self._fields[path] = (updated, value)

        walk(patch)

    def reconcile(self, snapshot: Mapping[str, Any], *, current=None) -> dict[str, Any]:
        updated = _number(snapshot.get("lastUpdatedAt"))
        result = dict(snapshot if current is None else current)
        if updated is None:
            self._fields.clear()
            return result
        self._fields = {
            path: field for path, field in self._fields.items() if field[0] > updated
        }
        for path, (timestamp, value) in self._fields.items():
            for part in reversed(path):
                value = [None] * part + [value] if isinstance(part, int) else {part: value}
            result = merge_status(result, value)
            result["lastUpdatedAt"] = max(_number(result.get("lastUpdatedAt")), timestamp)
        return result


def status_patch(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return only mapped values present in a partial MQTT report.

    Null, invalid and unknown fields do not overwrite the previous snapshot.
    A scheduled full snapshot remains authoritative for missing/null values.
    """
    def present(value):
        if isinstance(value, dict):
            return {key: cleaned for key, item in value.items() if (cleaned := present(item)) is not None} or None
        if isinstance(value, list):
            return value if any(item is not None for item in value) else None
        return value

    patch = {
        key: value for key, item in normalize_status(values).items()
        if (value := present(item)) is not None and value != {}
    }
    # A connection report can contain only latestDate/connectStatus. Its
    # timestamp must not make the cached vehicle telemetry appear fresh.
    if not any(isinstance(value, dict) and value for value in patch.values()):
        return {}
    return patch


def _mapped_field_names(patch: Mapping[str, Any]) -> list[str]:
    names = []
    for key, value in patch.items():
        if isinstance(value, dict):
            names.extend(f"{key}.{name}" for name in _mapped_field_names(value))
        elif isinstance(value, list):
            names.extend(f"{key}[{index}]" for index, item in enumerate(value) if item is not None)
        else:
            names.append(key)
    return sorted(names)


class MazdaMqttConnection:
    """One authenticated connection, with one reader and correlated read requests.

    Reports use the same encrypted service envelope as status responses. Only
    already mapped fields are exposed; unknown fields never invent HA states.
    """

    HEARTBEAT_INTERVAL = 20

    def __init__(self, config, password: str, on_notification: Callable[[str, dict], None]):
        self.config = config
        self._password = password
        self._on_notification = on_notification
        self._reader = None
        self._writer = None
        self._task = None
        self._secret_key = None
        self._pending = {}
        self._write_lock = asyncio.Lock()
        self._notifications = set(notification_topics(config))
        self._observed_topics = set(self._notifications)
        self._ping_sent = None
        self._report_stats = {}

    @property
    def notification_diagnostics(self) -> list[dict]:
        """Counters and selected numeric states; no credentials or device IDs."""
        now = monotonic()
        result = []
        device_ids = sorted(filter(None, {_topic_device_id(topic) for topic in self._observed_topics}))
        devices = {device: index for index, device in enumerate(device_ids, 1)}
        for index, topic in enumerate(sorted(self._observed_topics), 1):
            stats = self._report_stats.get(topic, {})
            result.append({
                "subscription": index,
                "device": devices.get(_topic_device_id(topic)),
                "kind": "properties/get/res" if topic.endswith("/properties/get/res") else "/".join(topic.split("/")[-2:]),
                "reports": stats.get("reports", 0),
                "mapped_reports": stats.get("mapped_reports", 0),
                "envelope_fields": stats.get("envelope_fields", []),
                "fields_seen": dict(stats.get("fields_seen", {})),
                "mapped_fields_seen": dict(stats.get("mapped_fields_seen", {})),
                "seconds_since_report": round(now - stats["last_report"], 1) if stats else None,
                "last_interval_seconds": stats.get("last_interval_seconds"),
                "enum_reports": [
                    {"age_seconds": round(now - report["received"], 1),
                     "lastUpdatedAt": report["lastUpdatedAt"],
                     "values": dict(report["values"])}
                    for report in stats.get("enum_reports", [])
                ],
            })
        return result

    def _record_report(self, topic, payload, values, patch) -> None:
        now = monotonic()
        stats = self._report_stats.setdefault(topic, {
            "reports": 0, "mapped_reports": 0, "fields_seen": {}, "mapped_fields_seen": {},
        })
        previous = stats.get("last_report")
        stats["last_interval_seconds"] = round(now - previous, 3) if previous is not None else None
        stats["last_report"] = now
        stats["reports"] += 1
        stats["mapped_reports"] += bool(patch)
        stats["envelope_fields"] = sorted(key for key in payload if isinstance(key, str) and len(key) <= 80)[:64]
        enum_values = {
            field: number for field in _ENUM_FIELDS
            if (number := _number(values.get(field))) is not None
        }
        if enum_values:
            reports = stats.setdefault("enum_reports", [])
            reports.append({
                "received": now, "lastUpdatedAt": normalize_status(values)["lastUpdatedAt"],
                "values": enum_values,
            })
            del reports[:-32]
        for target, names in (("fields_seen", values), ("mapped_fields_seen", _mapped_field_names(patch))):
            counts = stats[target]
            for name in names:
                if isinstance(name, str) and len(name) <= 80 and (name in counts or len(counts) < 256):
                    counts[name] = counts.get(name, 0) + 1

    @property
    def connected(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _write(self, packet: bytes) -> None:
        async with self._write_lock:
            self._writer.write(packet)
            await self._writer.drain()

    async def _receive(self):
        first, body = await _read_packet(self._reader)
        if first >> 4 == 14:
            raise MazdaMqttError("MQTT broker disconnected")
        if first >> 4 != 3:
            return first, body, None
        topic, payload, packet_id = _parse_publish(first, body)
        if packet_id is not None:
            await self._write(b"\x40\x04" + struct.pack("!H", packet_id) + b"\x00\x00")
        return first, body, (topic, payload)

    async def async_connect(self) -> None:
        """Bound the TLS, subscription and application login handshake."""
        try:
            async with asyncio.timeout(25):
                host, port = _broker(self.config)
                login_pub, login_sub, properties = _topic_groups(self.config)
                self._observed_topics.update(response for _, response in properties)
                login_id = _topic_device_id(login_pub)
                if not login_id or not self._notifications:
                    raise MazdaMqttError("MQTT notification topics are missing")
                context = await asyncio.to_thread(ssl.create_default_context)
                self._reader, self._writer = await asyncio.open_connection(
                    host, port, ssl=context, server_hostname=host,
                )
                await self._write(_connect_packet(login_id, login_id, self._password))
                first, body = await _read_packet(self._reader)
                if first != 0x20 or len(body) < 3 or body[1] != 0:
                    raise MazdaMqttError("MQTT connection was rejected")
                topics = list(dict.fromkeys([
                    login_sub, *(response for _, response in properties),
                    *sorted(self._notifications),
                ]))
                await self._write(_subscribe_packet(1, topics))
                # Retained notifications may precede SUBACK. Initial status is
                # refreshed once authenticated; retained messages are not changes.
                while True:
                    first, body, _ = await self._receive()
                    if first == 0x90:
                        break
                if len(body) < 4 or body[:2] != b"\x00\x01":
                    raise MazdaMqttError("Invalid MQTT subscription acknowledgement")
                length, pos = _variable_integer(body, 2)
                reasons = body[pos + length:]
                if len(reasons) != len(topics) or any(code not in (0, 1) for code in reasons):
                    raise MazdaMqttError("MQTT subscriptions were rejected")
                request_id = _request_id(login_id)
                await self._write(_publish_packet(login_pub, _login_payload(login_id, request_id)))
                while self._secret_key is None:
                    _, _, message = await self._receive()
                    if message is not None and message[0] == login_sub:
                        payload = message[1]
                        if payload.get("r") == request_id:
                            self._secret_key = _secret(payload)
                            if self._secret_key is None:
                                raise MazdaMqttError("MQTT application login was rejected")
                self._task = asyncio.create_task(self._async_read_loop())
        except BaseException:
            await self.async_close()
            raise

    async def _async_heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            if self._ping_sent is not None:
                raise MazdaMqttError("MQTT heartbeat timed out")
            self._ping_sent = asyncio.get_running_loop().time()
            await self._write(b"\xc0\x00")

    async def _async_read_loop(self) -> None:
        failure = MazdaMqttError("MQTT connection closed")
        heartbeat = asyncio.create_task(self._async_heartbeat())
        read_task = asyncio.create_task(self._receive())
        try:
            while True:
                # Never cancel a half-read packet to send a heartbeat.
                done, _ = await asyncio.wait({read_task, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
                if heartbeat in done:
                    await heartbeat
                first, body, message = await read_task
                read_task = asyncio.create_task(self._receive())
                if first == 0xD0:
                    self._ping_sent = None
                if message is None:
                    continue
                topic, payload = message
                future = self._pending.get((topic, payload.get("r")))
                if future is not None and not future.done():
                    future.set_result(payload)
                # Reconnect refreshes a full snapshot; retained data must not
                # overwrite it. Duplicate deliveries are safe partial merges.
                if topic in self._observed_topics and not first & 0x01:
                    values = _params(payload, self._secret_key)
                    patch = status_patch(values)
                    self._record_report(topic, payload, values, patch)
                    if patch and topic in self._notifications:
                        self._on_notification(topic, patch)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            failure = MazdaMqttError("MQTT notification connection failed")
            raise failure from err
        finally:
            for task in (read_task, heartbeat):
                task.cancel()
            for task in (read_task, heartbeat):
                with suppress(asyncio.CancelledError, Exception):
                    await task
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(failure)

    async def async_read_status(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """Read through this session, avoiding a second CONNECT with the same ID."""
        if not self.connected:
            raise MazdaMqttError("MQTT notification connection is unavailable")
        login_pub, _, properties = _topic_groups(config)
        login_id = _topic_device_id(login_pub)
        async with asyncio.timeout(25):
            for request_topic, response_topic in properties:
                device_id = _topic_device_id(request_topic)
                request_id = _request_id(device_id)
                future = asyncio.get_running_loop().create_future()
                pending_key = (response_topic, request_id)
                self._pending[pending_key] = future
                try:
                    await self._write(_publish_packet(request_topic, _condition_payload(
                        device_id, login_id, self._secret_key, request_id,
                    )))
                    payload = await future
                    values = _params(payload, self._secret_key)
                    if values:
                        return normalize_status(values)
                finally:
                    self._pending.pop(pending_key, None)
                    if not future.done():
                        future.cancel()
        raise MazdaMqttError("MQTT returned no vehicle status")

    async def async_wait_disconnected(self) -> None:
        await self._task

    async def async_close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            with suppress(OSError, ssl.SSLError, TimeoutError):
                async with asyncio.timeout(5):
                    await writer.wait_closed()


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _first(values: Mapping[str, Any], *keys: str) -> Any:
    return next((values[key] for key in keys if values.get(key) is not None), None)


def _enum(value: Any, mapping: Mapping[int, int]) -> int | None:
    return mapping.get(_number(value))


def normalize_status(values: Mapping[str, Any]) -> dict[str, Any]:
    """Map verified EU/Asia MQTT fields to the HTTPS status model."""
    updated = _first(values, "lastUpdatedTime", "lastUpdatedAt", "latestDate")
    if isinstance(updated, str):
        numeric = _number(updated)
        if numeric is not None:
            updated = numeric
        else:
            try:
                updated = int(datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                updated = None
    charge_minutes = _number(values.get("chargDeltMins"))
    inside = _number(values.get("vehicleTemperature"))
    target = _number(values.get("airConditioningSetTemperature"))
    humidity = _number(values.get("innerHumidity"))
    defrost = _enum(values.get("frontDefrostStatus"), {1: 0, 2: 1})
    if values.get("frontDefrosterSwitch") is not None:
        switch = _enum(values["frontDefrosterSwitch"], {0: 0, 1: 1})
        defrost = switch if defrost is None or switch == defrost else None
    tires = {}
    for position, field, warning in (
        ("leftFront", "lfTyrePressure", "lfPressureWarning"),
        ("rightFront", "rfTyrePressure", "rfPressureWarning"),
        ("leftBack", "lrTyrePressure", "lrPressureWarning"),
        ("rightBack", "rrTyrePressure", "rrPressureWarning"),
    ):
        pressure = _number(values.get(field))
        tires[position] = {
            "pressure": int(pressure) if pressure is not None else None,
            "status": _enum(values.get(warning), {0: 0}),
        }
    seats = {}
    for position, heat_field, vent_field in (
        ("leftFront", "driverSeatHeatStatus", "driverSeatAirStatus"),
        ("rightFront", "passengerSeatHeatStatus", "passengerSeatAirStatus"),
        ("leftBack", "leftBackSeatHeatStatus", "leftBackSeatVentilateStatus"),
        ("rightBack", "rightBackSeatHeatStatus", "rightBackSeatVentilateStatus"),
    ):
        heat = _enum(values.get(heat_field), {0: 0, 1: 1, 2: 2, 3: 3})
        vent = _enum(values.get(vent_field), {0: 0, 1: 1, 2: 2, 3: 3})
        mode = None
        if heat == 0 and vent == 0:
            mode = 0
        elif heat is not None and heat > 0 and (vent is None or vent == 0):
            mode = 1
        elif vent is not None and vent > 0 and (heat is None or heat == 0):
            mode = 2
        seats[position] = {
            "heatStatus": heat,
            "ventStatus": vent,
            "mode": mode,
        }
    ac_plug = _enum(values.get("acChargeGunConnectionState"), {0: 0})
    dc_plug = _enum(values.get("dcDhargeGunConnectionState"), {0: 0})
    return {
        "lastUpdatedAt": _number(updated),
        "vehicleStatus": {
            "soc": _number(_first(values, "soc", "socDsp", "remainPower")),
            "drvMileage": _number(_first(values, "remainedPowerMile", "totalResidualMileage")),
            "totalMileage": _number(values.get("totalOdometer")),
            "powerStatus": _enum(values.get("powerStatusFeedBack"), {0: 0, 2: 2}),
            "steeringWheelHeater": _enum(values.get("steeringWheelHeating"), {1: 0, 2: 1}),
            "status": _enum(values.get("carStatus"), {1: 1, 2: 2}),
        },
        "door": {"doors": [
            _number(values.get("driverDoor")), _number(values.get("passengerDoor")),
            _number(values.get("leftRearDoor")), _number(values.get("rightRearDoor")),
        ], "trunk": _number(values.get("trunk")),
            "driverLock": _enum(values.get("driverDoorLock"), {0: 0, 1: 1}),
            "passengerLock": _enum(values.get("passengerDoorLock"), {0: 0, 1: 1}),
        },
        "window": {"windows": [
            _number(values.get("diverWindow")), _number(values.get("passengerWindow")),
            _number(values.get("leftRearWindow")), _number(values.get("rightRearWindow")),
        ]},
        "tire": tires,
        "seat": seats,
        "lamp": {
            target: _enum(values.get(source), {0: 0})
            for target, source in (
                ("lowBeam", "lowBeam"), ("highBeam", "highBeam"),
                ("positionLamp", "positionLamp"),
                ("leftTurn", "turnLndicatorLeft"), ("rightTurn", "turnLndicatorRight"),
            )
        },
        "hvac": {
            "insideTemp": inside * 10 if inside is not None else None,
            "remoteTemp": target * 10 if target is not None else None,
            "acStatus": _number(values.get("airStatus")),
            "defrostStatus": defrost,
            "insideHumidity": int(humidity) if humidity is not None else None,
            "insidePm25": _number(values.get("pm25")),
        },
        "charge": {
            "chargeStatus": _enum(values.get("chargeStatus"), {3: 0}),
            "maxSocPercent": _number(values.get("chargeSoc")),
            "chargeConStatus": 0 if ac_plug == 0 and dc_plug == 0 else None,
            "dcChargeGunConnectStatus": dc_plug,
            "acChargeCurrent": _number(_first(values, "BattACChrgInCurr", "battACChrgInCurr")),
            "dcChargeCurrent": _number(_first(values, "BattDCChrgInCurr", "battDCChrgInCurr", "totalCurrent")),
            "remainChargeTime": charge_minutes,
        },
    }


async def _exchange(config: Mapping[str, Any], password: str) -> dict[str, Any]:
    host, port = _broker(config)
    login_pub, login_sub, properties = _topic_groups(config)
    login_id = _topic_device_id(login_pub)
    if not login_id:
        raise MazdaMqttError("MQTT login identity is missing")

    context = await asyncio.to_thread(ssl.create_default_context)
    reader, writer = await asyncio.open_connection(
        host, port, ssl=context, server_hostname=host,
    )
    try:
        writer.write(_connect_packet(login_id, login_id, password))
        await writer.drain()
        first, body = await _read_packet(reader)
        if first != 0x20 or len(body) < 2 or body[1] != 0:
            raise MazdaMqttError("MQTT connection was rejected")

        response_topics = [login_sub, *(response for _, response in properties)]
        writer.write(_subscribe_packet(1, response_topics))
        await writer.drain()
        await _read_packet(reader)  # SUBACK

        login_request_id = _request_id(login_id)
        writer.write(_publish_packet(login_pub, _login_payload(login_id, login_request_id)))
        await writer.drain()

        secret = None
        while secret is None:
            first, body = await _read_packet(reader)
            if first >> 4 != 3:
                continue
            _, payload, packet_id = _parse_publish(first, body)
            if packet_id is not None:
                writer.write(b"\x40\x04" + struct.pack("!H", packet_id) + b"\x00\x00")
                await writer.drain()
            secret = _secret(payload)

        for publish_topic, response_topic in properties:
            device_id = _topic_device_id(publish_topic)
            if not device_id:
                continue
            request_id = _request_id(device_id)
            writer.write(_publish_packet(
                publish_topic,
                _condition_payload(device_id, login_id, secret, request_id),
            ))
            await writer.drain()
            while True:
                first, body = await _read_packet(reader)
                if first >> 4 != 3:
                    continue
                topic, payload, packet_id = _parse_publish(first, body)
                if packet_id is not None:
                    writer.write(b"\x40\x04" + struct.pack("!H", packet_id) + b"\x00\x00")
                    await writer.drain()
                if topic != response_topic:
                    continue
                values = _params(payload, secret)
                if values:
                    return normalize_status(values)
                break
        raise MazdaMqttError("MQTT returned no vehicle status")
    finally:
        try:
            writer.write(b"\xe0\x02\x00\x00")
            await writer.drain()
        except (OSError, ConnectionError):
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionError, ssl.SSLError):
            pass


async def async_read_status(
    config: Mapping[str, Any], password: str, *, timeout: float = 25,
) -> dict[str, Any]:
    """Read one status snapshot within a single end-to-end timeout."""
    try:
        async with asyncio.timeout(timeout):
            return await _exchange(config, password)
    except TimeoutError as err:
        raise MazdaMqttError("MQTT status timed out") from err
