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
        """Startet Reauth, zeigt UI Hinweis."""
        self.reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()


    async def async_step_reauth_confirm(self, user_input=None):
        """Reauth muss E-Mail + Passwort + DeviceID erneut abfragen."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=STEP1_SCHEMA,
                description_placeholders={
                    "email": self.reauth_entry.data.get("email_enc", "<unknown>")
                }
            )

        # → danach ganz normal wie Step_user weitermachen
        return await self.async_step_user(user_input)


    def _get_reauth_entry(self):
        """Hilfsfunktion für Reauth."""
        return self.hass.config_entries.async_get_entry(self.context["entry_id"])


    # ------------------------------------------------------------------
    # STEP 1: Login
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input=None):
        """Step 1: Email + Passwort + DeviceID """
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP1_SCHEMA)

        self.api = Mazda6EApi(aiohttp_client.async_get_clientsession(self.hass))

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
        self.device_name = "Home Assistant"

        try:
            await self.api.send_device_login(
                self.token,
                self.email_enc,
                self.device_name
            )
        except Exception as ex:
            _LOGGER.exception(
                "Unknown error occurred during device login request: %s", ex
            )
            return self.async_abort(reason="device_login_failed")

        return await self.async_step_verify()


    # ------------------------------------------------------------------
    # STEP 2: Device Code bestätigen
    # ------------------------------------------------------------------
    async def async_step_verify(self, user_input=None):
        """Step 3: Code eingeben"""
        if user_input is None:
            return self.async_show_form(step_id="verify", data_schema=STEP3_SCHEMA)

        code = user_input["verification_code"]

        try:
            await self.api.verify_device_code(
                self.token,
                self.email_enc,
                code,
                self.device_name
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

        # Wird ein Reauth-Eintrag aktualisiert?
        if self.reauth_entry:
            return self._handle_reauth_success()

        # Normale Einrichtung
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
    # Reauth Abschluss
    # ------------------------------------------------------------------
    def _handle_reauth_success(self):
        """Eintrag aktualisieren & Flow beenden."""
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
