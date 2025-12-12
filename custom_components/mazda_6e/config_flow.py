import logging
import uuid

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import aiohttp_client

from .const import DOMAIN
from .api import Mazda6EApi

_LOGGER = logging.getLogger(__name__)

STEP1_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Required("deviceid", default=str(uuid.uuid4())): str})

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
        self.reauth_entry = None   # <--- für Reauth


    # ------------------------------------------------------------------
    # STEP 0: Re-Auth starten
    # ------------------------------------------------------------------
    async def async_step_reauth(self, user_input=None):
        """starts reauth, showing ui hint."""
        self.reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()


    async def async_step_reauth_confirm(self, user_input=None):
        """reauth have to ask for E-Mail + Password + DeviceID again."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=STEP1_SCHEMA,
                description_placeholders={
                    "email": self.reauth_entry.data.get("email_enc", "<unknown>")
                }
            )

        return await self.async_step_user(user_input)


    def _get_reauth_entry(self):
        """Helper function for Reauth."""
        return self.hass.config_entries.async_get_entry(self.context["entry_id"])


    # ------------------------------------------------------------------
    # STEP 1: Login with mail + password
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input=None):
        """Step 1: Email + Password + DeviceID """
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP1_SCHEMA)

        self.api = Mazda6EApi(aiohttp_client.async_get_clientsession(self.hass), None, None, user_input["deviceid"])

        try:
            data = await self.api.login_email_password(
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD]
            )
        except Exception as err:
            _LOGGER.error("Login failed: %s", err)
            return self.async_show_form(
                step_id="user",
                data_schema=STEP1_SCHEMA,
                errors={"base": "login_failed"},
            )

        self.email_enc = user_input[CONF_EMAIL]
        self.deviceid = user_input["deviceid"]
        self.token = data["token"]

        try:
            await self.api.send_device_login(
                self.token,
                self.email_enc
            )
        except Exception as ex:
            _LOGGER.exception(
                "Unknown error occurred during device login request: %s", ex
            )
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
        except Exception as ex:
            _LOGGER.exception(
                "Unknown error occurred during email verify request: %s", ex
            )
            return self.async_show_form(
                step_id="verify",
                data_schema=STEP3_SCHEMA,
                errors={"base": "verification_failed"}
            )

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

        return self.async_abort(reason="reauth_successful")
