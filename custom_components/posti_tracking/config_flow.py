import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_LANGUAGE,
    CONF_MAX_SHIPMENTS,
    CONF_STALE_SHIPMENT_DAY_LIMIT,
    CONF_COMPLETED_SHIPMENT_DAYS_SHOWN,
    LANGUAGES,
    CONF_PRIORITIZE_UNDELIVERED
)
from .session import PostiException, PostiSession

_LOGGER = logging.getLogger(__name__)

CONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_LANGUAGE): vol.All(cv.string, vol.In(LANGUAGES)),
        vol.Required(CONF_PRIORITIZE_UNDELIVERED, default=True): cv.boolean,
        vol.Required(CONF_MAX_SHIPMENTS, default=5): cv.positive_int,
        vol.Required(CONF_STALE_SHIPMENT_DAY_LIMIT, default=15): cv.positive_int,
        vol.Required(CONF_COMPLETED_SHIPMENT_DAYS_SHOWN, default=3): cv.positive_int
    }
)

RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_LANGUAGE): vol.All(cv.string, vol.In(LANGUAGES)),
        vol.Required(CONF_PRIORITIZE_UNDELIVERED): cv.boolean,
        vol.Required(CONF_MAX_SHIPMENTS): cv.positive_int,
        vol.Required(CONF_STALE_SHIPMENT_DAY_LIMIT): cv.positive_int,
        vol.Required(CONF_COMPLETED_SHIPMENT_DAYS_SHOWN): cv.positive_int
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, any]) -> str:
    try:
        session = PostiSession(data["username"], data["password"])
        await hass.async_add_executor_job(session.authenticate)

    except PostiException:
        raise InvalidAuth

    return data["username"]


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, any] = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=CONFIGURE_SCHEMA)

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(title=info, data=user_input)

        return self.async_show_form(step_id="user", data_schema=CONFIGURE_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, any] = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="init", data_schema=vol.Schema(
                    {
                        vol.Required(CONF_PASSWORD, default=self._config_entry.data.get(CONF_PASSWORD)): cv.string,
                        vol.Required(CONF_LANGUAGE, default=self._config_entry.data.get(CONF_LANGUAGE)): vol.All(cv.string, vol.In(LANGUAGES)),
                        vol.Required(CONF_PRIORITIZE_UNDELIVERED, default=self._config_entry.data.get(CONF_PRIORITIZE_UNDELIVERED)): cv.boolean,
                        vol.Optional(CONF_MAX_SHIPMENTS, default=self._config_entry.data.get(CONF_MAX_SHIPMENTS)): cv.positive_int,
                        vol.Optional(CONF_STALE_SHIPMENT_DAY_LIMIT, default=self._config_entry.data.get(CONF_STALE_SHIPMENT_DAY_LIMIT)): cv.positive_int,
                        vol.Optional(CONF_COMPLETED_SHIPMENT_DAYS_SHOWN, default=self._config_entry.data.get(CONF_COMPLETED_SHIPMENT_DAYS_SHOWN)): cv.positive_int
                    })
            )

        errors = {}

        try:
            user_input["username"] = self._config_entry.data[CONF_USERNAME]
            await validate_input(self.hass, user_input)
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            self.hass.config_entries.async_update_entry(self._config_entry, data=user_input, options=self._config_entry.options)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(step_id="init", data_schema=RECONFIGURE_SCHEMA, errors=errors)


class InvalidAuth(HomeAssistantError):
    """Error to indicate authentication credentials where invalid"""
