"""Charge-limit API contract tests."""

import asyncio
import base64
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


ROOT = Path(__file__).parents[1] / "custom_components" / "mazda_6e"


def load_module(monkeypatch, name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api_context(monkeypatch):
    package = ModuleType("api_test")
    package.__path__ = [str(ROOT)]
    exceptions = ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    monkeypatch.setitem(sys.modules, "api_test", package)
    monkeypatch.setitem(sys.modules, "homeassistant", ModuleType("homeassistant"))
    monkeypatch.setitem(sys.modules, "homeassistant.exceptions", exceptions)
    const = load_module(monkeypatch, "api_test.const", "const.py")
    crypto = load_module(monkeypatch, "api_test.credential_crypto", "credential_crypto.py")
    load_module(monkeypatch, "api_test.models", "models.py")
    api_module = load_module(monkeypatch, "api_test.api", "api.py")
    return api_module, const, crypto


def make_api(api_module, const, crypto):
    """Build an API with a real control key and its matching encrypted serial."""
    public_key, private_key = crypto.generate_control_key_pair()
    key = serialization.load_der_public_key(base64.b64decode(public_key))
    encrypted_serial = base64.encodebytes(key.encrypt(b"serial", padding.PKCS1v15())).decode()
    api = api_module.Mazda6EApi(
        None,
        "token",
        "refresh",
        "device",
        control_private_key=private_key,
        region=const.REGION_EUROPE,
    )
    return api, key, encrypted_serial


def test_set_charge_limit_uses_observed_contract(api_context):
    """Use serial type 2 and the captured charge_max request fields."""
    api, key, encrypted_serial = make_api(*api_context)
    api._request = AsyncMock(side_effect=[
        {"data": encrypted_serial},
        {"data": {"commandId": "command-id"}},
        {"data": {"resultCode": 0, "errorMsg": "success"}},
    ])

    result = asyncio.run(api.async_set_charge_limit(123, 85))

    assert result["resultCode"] == 0
    calls = api._request.await_args_list
    assert calls[0].args[0].endswith("/serial-no/get")
    assert calls[0].args[2] == {"type": "2"}
    assert calls[1].args[0].endswith("/charge/percentage")
    payload = calls[1].args[2]
    signature = payload.pop("sign")
    assert payload == {
        "chargePercentageMax": 85,
        "command": "charge_max",
        "rcToken": "",
        "seriralNo": "serial",
        "vehicleId": "123",
    }
    key.verify(
        base64.b64decode(signature),
        b"chargePercentageMax=85&seriralNo=serial&vehicleId=123",
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    assert calls[2].args[0].endswith("/control/control-result")
    assert calls[2].args[2] == {"commandId": "command-id", "vehicleId": "123"}


def test_set_charge_limit_accepts_code_1000_only_when_status_matches(api_context):
    """A non-responsive controller is acceptable only if the requested state is active."""
    api, _, encrypted_serial = make_api(*api_context)
    api._request = AsyncMock(side_effect=[
        {"data": encrypted_serial},
        {"data": {"commandId": "command-id"}},
    ])
    api._async_wait_for_control_result = AsyncMock(
        side_effect=RuntimeError("Control failed with result code 1000"),
    )
    api.async_get_vehicle_status = AsyncMock(
        return_value={"charge": {"maxSocPercent": 80}},
    )

    result = asyncio.run(api.async_set_charge_limit(123, 80))

    assert result["resultCode"] == 1000

    api._async_set_charge_limit_once = AsyncMock(side_effect=[
        RuntimeError("Control failed with result code 1000"),
        {"resultCode": 0, "errorMsg": "success"},
    ])
    api.async_get_vehicle_status = AsyncMock(
        return_value={"charge": {"maxSocPercent": 80}},
    )

    result = asyncio.run(api.async_set_charge_limit(123, 85))

    assert result["resultCode"] == 0
    assert api._async_set_charge_limit_once.await_count == 2
