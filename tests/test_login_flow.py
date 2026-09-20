"""Offline flow unit tests with a minimal HA facade, not HA integration tests."""
import asyncio
import base64
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).parents[1] / "custom_components" / "mazda_6e"


@pytest.fixture
def module(monkeypatch):
    class Flow:
        def __init_subclass__(cls, **kwargs):
            pass
        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}
        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}
        def async_abort(self, **kwargs):
            return {"type": "abort", **kwargs}

    modules = {name: ModuleType(name) for name in (
        "homeassistant", "homeassistant.config_entries", "homeassistant.const",
        "homeassistant.helpers", "homeassistant.helpers.aiohttp_client",
        "login_test", "login_test.api", "login_test.const",
    )}
    modules["login_test"].__path__ = [str(ROOT)]
    modules["homeassistant.config_entries"].ConfigFlow = Flow
    modules["homeassistant.const"].CONF_EMAIL = "email"
    modules["homeassistant.const"].CONF_PASSWORD = "password"
    modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda hass: None
    modules["login_test.const"].DOMAIN = "mazda_6e"
    api = SimpleNamespace(refresh="synthetic-refresh", login_email_password=AsyncMock(return_value={
        "token": "synthetic-token", "refreshToken": "synthetic-refresh", "emailVerify": True,
    }), send_device_login=AsyncMock(), verify_device_code=AsyncMock())
    modules["login_test.api"].Mazda6EApi = Mock(return_value=api)
    for name, value in modules.items():
        monkeypatch.setitem(sys.modules, name, value)
    for name in ("credential_crypto", "config_flow"):
        spec = importlib.util.spec_from_file_location("login_test." + name, ROOT / (name + ".py"))
        loaded = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, loaded)
        spec.loader.exec_module(loaded)
    return loaded, api


def new_flow(module):
    flow = module.Mazda6eConfigFlow()
    flow.hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=Mock(), async_reload=Mock(return_value=None)), async_create_task=Mock())
    return flow


def test_plaintext_login_verification_and_unique_identity(module):
    mod, api = module
    async def run():
        flow = new_flow(mod)
        assert set(mod.STEP1_SCHEMA.schema) == {"email", "password"}
        result = await flow.async_step_user({"email": "test@example.invalid", "password": "secret"})
        assert result["step_id"] == "verify"
        args = api.login_email_password.call_args.args
        assert args[0] != "test@example.invalid" and args[1] != "secret"
        assert len(base64.b64decode(args[0])) == 256
        api.verify_device_code.side_effect = ValueError("private-detail")
        assert (await flow.async_step_verify({"verification_code": "bad"}))["errors"]
        api.verify_device_code.side_effect = None
        result = await flow.async_step_verify({"verification_code": "123456"})
        assert result["type"] == "create_entry"
        assert set(result["data"]) == {"token", "refresh", "email_enc", "deviceid"}
        other = new_flow(mod)
        await other.async_step_user({"email": "test@example.invalid", "password": "secret"})
        assert other.deviceid != flow.deviceid
    asyncio.run(run())


def test_reauth_keeps_identity_and_retry_step(module, caplog):
    mod, api = module
    async def run():
        flow = new_flow(mod)
        entry = SimpleNamespace(data={"deviceid": "existing-device"}, entry_id="entry")
        flow.context = {"entry_id": "entry"}
        flow.hass.config_entries.async_get_entry = Mock(return_value=entry)
        assert (await flow.async_step_reauth())["step_id"] == "reauth_confirm"
        api.login_email_password.side_effect = ValueError("private-detail")
        result = await flow.async_step_reauth_confirm({"email": "test@example.invalid", "password": "secret"})
        assert result["step_id"] == "reauth_confirm"
        assert flow.deviceid == "existing-device"
        api.login_email_password.side_effect = None
        api.login_email_password.return_value["emailVerify"] = False
        result = await flow.async_step_reauth_confirm({"email": "test@example.invalid", "password": "secret"})
        assert result["reason"] == "reauth_successful"
        assert flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]["deviceid"] == "existing-device"
        api.send_device_login.assert_not_called()
        assert "private-detail" not in caplog.text
    asyncio.run(run())


def test_crypto_unicode_and_chunking(module):
    import hashlib
    crypto = sys.modules["login_test.credential_crypto"]
    assert hashlib.sha256(base64.b64decode(crypto.SERVER_PUBLIC_KEY)).hexdigest() == "41c4d777606e1a1185cb2a7604b161853b45406f912bc78551f9fb9c0eb7f1f4"
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = base64.b64encode(private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
    value = "a" * 244 + "\u00f8" + "\U0001f511" * 70
    encoded = crypto.encrypt_credential(value, public)
    raw = base64.b64decode(encoded)
    assert base64.encodebytes(raw).decode() == encoded
    assert b"".join(private.decrypt(raw[i:i+256], padding.PKCS1v15()) for i in range(0, len(raw), 256)) == value.encode()
