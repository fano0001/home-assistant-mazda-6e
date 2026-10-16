"""Minimal read-only MQTT 5 transport for Asia vehicle status."""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import ssl
import struct
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class MazdaMqttError(Exception):
    """Raised when a read-only MQTT status exchange fails."""


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
    raise MazdaMqttError("Truncated MQTT property length")


def _parse_publish(first: int, body: bytes) -> tuple[str, dict[str, Any], int | None]:
    if len(body) < 2:
        raise MazdaMqttError("Invalid MQTT publish packet")
    topic_length = struct.unpack("!H", body[:2])[0]
    pos = 2
    topic = body[pos:pos + topic_length].decode()
    pos += topic_length
    packet_id = None
    if (first >> 1) & 0x03:
        packet_id = struct.unpack("!H", body[pos:pos + 2])[0]
        pos += 2
    property_length, pos = _variable_integer(body, pos)
    pos += property_length
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
        encrypted = payload.get(field)
        if not isinstance(encrypted, str):
            continue
        try:
            services = _decrypt_services(encrypted, key, request_id)
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        for service in services:
            values = service.get("params") if isinstance(service, dict) else None
            if isinstance(values, dict):
                result.update(values)
    return result


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _first(values: Mapping[str, Any], *keys: str) -> Any:
    return next((values[key] for key in keys if values.get(key) is not None), None)


def normalize_status(values: Mapping[str, Any]) -> dict[str, Any]:
    """Map only Asia fields whose semantics are established."""
    updated = _first(values, "lastUpdatedTime", "lastUpdatedAt", "latestDate")
    if isinstance(updated, str):
        try:
            updated = int(datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            updated = None
    charge_minutes = _number(values.get("chargDeltMins"))
    if charge_minutes == 8191:
        charge_minutes = None
    inside = _number(values.get("vehicleTemperature"))
    target = _number(values.get("airConditioningSetTemperature"))
    return {
        "lastUpdatedAt": _number(updated),
        "vehicleStatus": {
            "soc": _number(_first(values, "soc", "socDsp", "remainPower")),
            "drvMileage": _number(_first(values, "remainedPowerMile", "totalResidualMileage")),
            "totalMileage": _number(values.get("totalOdometer")),
        },
        "door": {"doors": [
            _number(values.get("driverDoor")), _number(values.get("passengerDoor")),
            _number(values.get("leftRearDoor")), _number(values.get("rightRearDoor")),
        ], "trunk": _number(values.get("trunk"))},
        "window": {"windows": [
            _number(values.get("diverWindow")), _number(values.get("passengerWindow")),
            _number(values.get("leftRearWindow")), _number(values.get("rightRearWindow")),
        ]},
        "hvac": {
            "insideTemp": inside * 10 if inside is not None else None,
            "remoteTemp": target * 10 if target is not None else None,
            "acStatus": _number(values.get("airStatus")),
        },
        "charge": {
            "acChargeCurrent": _number(_first(values, "BattACChrgInCurr", "battACChrgInCurr")),
            "dcChargeCurrent": _number(_first(values, "BattDCChrgInCurr", "battDCChrgInCurr")),
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
