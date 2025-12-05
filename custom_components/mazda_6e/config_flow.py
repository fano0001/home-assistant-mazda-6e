import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .const import DOMAIN
from .api import Mazda6EApi

_LOGGER = logging.getLogger(__name__)

STEP1_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Required("deviceid"): str
})

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

    async def async_step_user(self, user_input=None):
        """Step 1: Email + Passwort + DeviceID """
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP1_SCHEMA)

        self.api = Mazda6EApi(self.hass.helpers.aiohttp_client.async_get_clientsession())

        try:
            data = await self.api.login_email_password(
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                user_input["deviceid"]
            )
        except Exception as err:
            _LOGGER.error("Login failed: %s", err)
            return self.async_show_form(
                step_id="user",
                data_schema=STEP1_SCHEMA,
                errors={"base": "login_failed"},
            )

        # Speichern für Schritt 2+3
        self.email_enc = user_input[CONF_EMAIL]
        self.deviceid = user_input["deviceid"]
        self.token = data["token"]
        self.device_name = "Home Assistant"

        # Starte Device Login
        try:
            await self.api.send_device_login(
                self.token,
                self.email_enc,
                self.device_name,
                self.deviceid
            )
        except Exception as ex:
            _LOGGER.exception(
                "Unknown error occurred during device login request: %s", ex
            )
            return self.async_abort(reason="device_login_failed")

        return await self.async_step_verify()

    async def async_step_verify(self, user_input=None):
        """Step 3: Code eingeben"""
        if user_input is None:
            return self.async_show_form(step_id="verify", data_schema=STEP3_SCHEMA)

        code = user_input["verification_code"]

        try:
            ok = await self.api.verify_device_code(
                self.token,
                self.email_enc,
                code,
                self.device_name,
                self.deviceid
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

        # Erfolg → Integration anlegen
        return self.async_create_entry(
            title="Mazda 6e",
            data={
                "token": self.token,
                "refresh": self.api.refresh,
                "email_enc": self.email_enc,
                "deviceid": self.deviceid
            }
        )
