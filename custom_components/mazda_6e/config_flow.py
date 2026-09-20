import logging
import uuid

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import aiohttp_client

from .const import DOMAIN
from .api import Mazda6EApi
from .credential_crypto import encrypt_credential

_LOGGER = logging.getLogger(__name__)

STEP1_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): str,
    vol.Required(CONF_PASSWORD): str})

STEP3_SCHEMA = vol.Schema({
    vol.Required("verification_code"): str
})


class Mazda6eConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.device_name = None
        self.token = None
        self.deviceid = None
        self.email_enc = None
        self.api = None
        self.reauth_entry = None  # <--- for Reauth

    # ------------------------------------------------------------------
    # STEP 0: Re-Auth starten
    # ------------------------------------------------------------------
    async def async_step_reauth(self, user_input=None):
        """starts reauth, showing ui hint."""
        self.reauth_entry = self._get_reauth_entry()
        self.deviceid = self.reauth_entry.data.get("deviceid") or str(uuid.uuid4())
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask for ordinary credentials, preserving the registered device."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=STEP1_SCHEMA,
            )

        return await self.async_step_user(user_input)

    def _get_reauth_entry(self):
        """Helper function for Reauth."""
        return self.hass.config_entries.async_get_entry(self.context["entry_id"])

    # ------------------------------------------------------------------
    # STEP 1: Login with mail + password
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input=None):
        """Accept ordinary credentials and generate one device ID per flow."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP1_SCHEMA)

        if self.deviceid is None:
            self.deviceid = str(uuid.uuid4())
        self.api = Mazda6EApi(aiohttp_client.async_get_clientsession(self.hass), None, None, self.deviceid)

        try:
            if not user_input[CONF_EMAIL] or not user_input[CONF_PASSWORD]:
                raise ValueError("Empty credentials")
            self.email_enc = encrypt_credential(user_input[CONF_EMAIL])
            password_enc = encrypt_credential(user_input[CONF_PASSWORD])
            data = await self.api.login_email_password(self.email_enc, password_enc)
            if not isinstance(data.get("emailVerify"), bool):
                raise ValueError("Missing verification state")
            if not all(isinstance(data.get(key), str) and data[key] for key in ("token", "refreshToken")):
                raise ValueError("Incomplete token pair")
        except Exception:
            _LOGGER.error("Login failed")
            return self.async_show_form(
                step_id="reauth_confirm" if self.reauth_entry else "user",
                data_schema=STEP1_SCHEMA,
                errors={"base": "login_failed"},
            )

        self.token = data["token"]
        if data["emailVerify"] is False:
            return self._finish_login()

        try:
            await self.api.send_device_login(
                self.token,
                self.email_enc
            )
        except Exception:
            _LOGGER.error("Device login request failed")
            return self.async_abort(reason="device_login_failed")

        return await self.async_step_verify()

    # ------------------------------------------------------------------
    # STEP 2: confirm device with code
    # ------------------------------------------------------------------
    async def async_step_verify(self, user_input=None):
        """Step 3: insert code from mail"""
        if user_input is None:
            return self.async_show_form(step_id="verify", data_schema=STEP3_SCHEMA)

        code = user_input["verification_code"]

        try:
            await self.api.verify_device_code(
                self.token,
                self.email_enc,
                code
            )
        except Exception:
            _LOGGER.error("Email verification failed")
            return self.async_show_form(
                step_id="verify",
                data_schema=STEP3_SCHEMA,
                errors={"base": "verification_failed"}
            )

        return self._finish_login()

    def _finish_login(self):
        """Persist encrypted email and tokens only; never the password."""
        if self.reauth_entry:
            return self._handle_reauth_success()

        return self.async_create_entry(
            title="Mazda 6e",
            data={
                "token": self.token,
                "refresh": self.api.refresh,
                "email_enc": self.email_enc,
                "deviceid": self.deviceid
            }
        )

    # ------------------------------------------------------------------
    #  finish Reauth
    # ------------------------------------------------------------------
    def _handle_reauth_success(self):
        """update entry & finish Flow."""
        self.hass.config_entries.async_update_entry(
            self.reauth_entry,
            data={
                "token": self.token,
                "refresh": self.api.refresh,
                "email_enc": self.email_enc,
                "deviceid": self.deviceid
            }
        )

        self.hass.async_create_task(
            self.hass.config_entries.async_reload(self.reauth_entry.entry_id)
        )

        return self.async_abort(reason="reauth_successful")
