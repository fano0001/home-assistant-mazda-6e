"""Vehicle discovery API contract tests."""

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
def api_context(monkeypatch):
    package = ModuleType("vehicle_test")
    package.__path__ = [str(ROOT)]
    exceptions = ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    monkeypatch.setitem(sys.modules, "vehicle_test", package)
    monkeypatch.setitem(sys.modules, "homeassistant", ModuleType("homeassistant"))
    monkeypatch.setitem(sys.modules, "homeassistant.exceptions", exceptions)
    const = load_module(monkeypatch, "vehicle_test.const", "const.py")
    load_module(monkeypatch, "vehicle_test.credential_crypto", "credential_crypto.py")
    load_module(monkeypatch, "vehicle_test.models", "models.py")
    api_module = load_module(monkeypatch, "vehicle_test.api", "api.py")
    return api_module, const


@pytest.mark.parametrize("region_name", ["REGION_EUROPE", "REGION_ASIA"])
def test_empty_legacy_response_tries_car_endpoint(api_context, region_name):
    """A successful empty legacy response must not prevent vehicle discovery."""
    api_module, const = api_context
    api = api_module.Mazda6EApi(
        None, token="token", deviceid="device", region=getattr(const, region_name),
    )
    api._request = AsyncMock(side_effect=[
        {"success": True, "data": []},
        {
            "success": True,
            "data": [{
                "carId": "752229328010919936",
                "vin": "TESTVIN",
                "modelName": "Mazda 6e",
                "carName": "Shared car",
            }],
        },
    ])

    vehicles = asyncio.run(api.async_get_vehicles())

    assert api._request.await_count == 2
    calls = api._request.await_args_list
    assert calls[0].args[0].endswith("/cma-app-user/api/vehicle/vehicles")
    assert calls[1].args[0].endswith("/cma-app-user/api/car/vehicles")
    assert calls[1].args[2] == {}
    assert vehicles[0].vehicle_id == "752229328010919936"


def test_europe_keeps_vehicle_endpoint(api_context):
    """A non-empty response does not make an unnecessary second request."""
    api_module, const = api_context
    api = api_module.Mazda6EApi(
        None, token="token", deviceid="device", region=const.REGION_EUROPE,
    )
    api._request = AsyncMock(return_value={
        "success": True,
        "data": [{"vehicleId": 123, "vin": "TESTVIN", "modelName": "Mazda 6e"}],
    })

    vehicles = asyncio.run(api.async_get_vehicles())

    assert api._request.await_count == 1
    assert api._request.await_args.args[0].endswith("/cma-app-user/api/vehicle/vehicles")
    assert vehicles[0].vehicle_id == 123


def test_alternative_failure_preserves_empty_legacy_response(api_context):
    """An unavailable alternative route must not break an account with no cars."""
    api_module, const = api_context
    api = api_module.Mazda6EApi(
        None, token="token", deviceid="device", region=const.REGION_EUROPE,
    )
    api._request = AsyncMock(side_effect=[
        {"success": True, "data": []},
        api_module.MazdaApiError("not available"),
    ])

    assert asyncio.run(api.async_get_vehicles()) == []
