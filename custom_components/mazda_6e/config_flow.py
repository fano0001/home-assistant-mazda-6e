from homeassistant import config_entries
import voluptuous as vol

from .const import DOMAIN


class Mazda6eConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title="Mazda 6e",
                data=user_input
            )

        schema = vol.Schema({
            vol.Required("email_enc"): str,
            vol.Required("password_enc"): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema
        )
