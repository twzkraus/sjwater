import hashlib
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CannotConnect, InvalidAuth, SJWaterHubApiClient
from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)
STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict) -> dict:
    """Validate the user input allows us to connect."""
    session = async_get_clientsession(hass)
    client = SJWaterHubApiClient(data[CONF_USERNAME], data[CONF_PASSWORD], session)

    await client.async_verify_credentials()

    return {"title": "SJ Water Hub"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SJ Water Hub."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            normalized_username = user_input[CONF_USERNAME].strip().casefold()
            acct_id = hashlib.sha256(normalized_username.encode()).hexdigest()
            await self.async_set_unique_id(acct_id)
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
                return self.async_create_entry(title=info["title"], data=user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating SJ Water Hub credentials")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data):
        """Start reauthentication after stored credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask the user to update the stored password."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reauth_entry_not_found")

        errors = {}
        if user_input is not None:
            updated_data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await validate_input(self.hass, updated_data)
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                    reason="reauth_successful",
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error reauthenticating with SJ Water Hub")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
        )
