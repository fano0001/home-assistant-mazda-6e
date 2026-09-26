import logging
import uuid

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_REGION
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType, SelectSelector, SelectSelectorConfig

from .const import CONF_CONTROL_PIN, DOMAIN, REGION_EUROPE, REGION_ASIA
from .api import Mazda6EApi, MazdaLoginError
from .credential_crypto import encrypt_credential, generate_control_key_pair

_LOGGER = logging.getLogger(__name__)

STEP1_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Required(CONF_REGION, default=REGION_EUROPE): SelectSelector(
        SelectSelectorConfig(
            options=[
                REGION_EUROPE,
                REGION_ASIA,
            ],
            mode="dropdown",
        )
    ),
    vol.Optional(CONF_CONTROL_PIN): TextSelector(
        TextSelectorConfig(type=TextSelectorType.PASSWORD)
    )})

REAUTH_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): str,
    vol.Required(CONF_PASSWORD): str})

RECONFIGURE_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Required(CONF_REGION, default=REGION_EUROPE): SelectSelector(
        SelectSelectorConfig(
            options=[
                REGION_EUROPE,
                REGION_ASIA,
            ],
            mode="dropdown",
        )
    ),
    vol.Required(CONF_CONTROL_PIN): TextSelector(
        TextSelectorConfig(type=TextSelectorType.PASSWORD)
    )})

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
        self.reconfigure_entry = None
        self.control_public_key = None
        self.control_private_key = None
        self.control_pin = None
        self.region = None

    # ------------------------------------------------------------------
    # STEP 0: Start reauthentication
    # ------------------------------------------------------------------
    async def async_step_reauth(self, user_input=None):
        """starts reauth, showing ui hint."""
        self.reauth_entry = self._get_reauth_entry()
        self.deviceid = self.reauth_entry.data.get("deviceid") or str(uuid.uuid4())
        self.control_pin = self.reauth_entry.data.get(CONF_CONTROL_PIN)
        self.region = self.reauth_entry.data.get(CONF_REGION, REGION_EUROPE)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask for ordinary credentials, preserving the registered device."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=REAUTH_SCHEMA,
            )

        return await self.async_step_user(user_input)

    def _get_reauth_entry(self):
        """Helper function for Reauth."""
        return self.hass.config_entries.async_get_entry(self.context["entry_id"])

    async def async_step_reconfigure(self, user_input=None):
        """Update credentials required for cloud vehicle controls."""
        if self.reconfigure_entry is None:
            self.reconfigure_entry = self.hass.config_entries.async_get_entry(
                self.context["entry_id"]
            )
            self.deviceid = self.reconfigure_entry.data.get("deviceid") or str(uuid.uuid4())

        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=RECONFIGURE_SCHEMA,
            )

        return await self.async_step_user(user_input)

    # ------------------------------------------------------------------
    # STEP 1: Login with mail + password
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input=None):
        """Accept ordinary credentials and generate one device ID per flow."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP1_SCHEMA)

        step_id = "reconfigure" if self.reconfigure_entry else (
            "reauth_confirm" if self.reauth_entry else "user"
        )
        data_schema = RECONFIGURE_SCHEMA if self.reconfigure_entry else (
            REAUTH_SCHEMA if self.reauth_entry else STEP1_SCHEMA
        )
        if self.deviceid is None:
            self.deviceid = str(uuid.uuid4())
        self.region = user_input.get(CONF_REGION, self.region)
        self.control_public_key, self.control_private_key = generate_control_key_pair()
        self.api = Mazda6EApi(
            aiohttp_client.async_get_clientsession(self.hass), None, None, self.deviceid,
            self.control_public_key, self.control_private_key, region=self.region
        )

        try:
            if not user_input[CONF_EMAIL] or not user_input[CONF_PASSWORD]:
                raise ValueError("Empty credentials")
            if CONF_CONTROL_PIN in user_input:
                self.control_pin = user_input[CONF_CONTROL_PIN]
            if self.control_pin is not None and (
                    len(self.control_pin) != 6 or not self.control_pin.isdigit()
            ):
                return self.async_show_form(
                    step_id=step_id,
                    data_schema=data_schema,
                    errors={CONF_CONTROL_PIN: "invalid_control_pin"},
                )
            self.email_enc = encrypt_credential(user_input[CONF_EMAIL])
            password_enc = encrypt_credential(user_input[CONF_PASSWORD])
            data = await self.api.login_email_password(self.email_enc, password_enc)
            if not isinstance(data.get("emailVerify"), bool):
                raise ValueError("Missing verification state")
            if not all(isinstance(data.get(key), str) and data[key] for key in ("token", "refreshToken")):
                raise ValueError("Incomplete token pair")
        except MazdaLoginError as err:
            _LOGGER.error("Login failed with Mazda response code %s", err.code)
            return self.async_show_form(
                step_id=step_id,
                data_schema=data_schema,
                errors={"base": "login_failed"},
            )
        except Exception:
            _LOGGER.error("Login failed")
            return self.async_show_form(
                step_id=step_id,
                data_schema=data_schema,
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
        """Persist the session and vehicle-control credentials."""
        if self.reconfigure_entry or self.reauth_entry:
            return self._handle_existing_entry_success()

        return self.async_create_entry(
            title="Mazda 6e",
            data={
                "token": self.token,
                "refresh": self.api.refresh,
                "email_enc": self.email_enc,
                "deviceid": self.deviceid,
                "control_private_key": self.control_private_key,
                CONF_CONTROL_PIN: self.control_pin,
                CONF_REGION: self.region
            }
        )

    def _handle_existing_entry_success(self):
        """Update and reload an existing config entry."""
        entry = self.reconfigure_entry or self.reauth_entry
        self.hass.config_entries.async_update_entry(
            entry,
            data={
                "token": self.token,
                "refresh": self.api.refresh,
                "email_enc": self.email_enc,
                "deviceid": self.deviceid,
                "control_private_key": self.control_private_key,
                CONF_CONTROL_PIN: self.control_pin,
                CONF_REGION: self.region
            },
        )
        self.hass.async_create_task(
            self.hass.config_entries.async_reload(entry.entry_id)
        )
        return self.async_abort(
            reason="reconfigure_successful" if self.reconfigure_entry else "reauth_successful"
        )
