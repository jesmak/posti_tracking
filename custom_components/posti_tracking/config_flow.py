"""Config flow: an account is added with the user name and password of OmaPosti.

Logging in gives the tokens, which are saved with the entry. The coordinator
renews and saves them later; when Posti no longer accepts the password,
reauthentication asks for it again. Settings are changed by reconfiguring.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_COMPLETED_SHIPMENT_DAYS_SHOWN,
    CONF_INCLUDE_PICKUP_DETAILS,
    CONF_LANGUAGE,
    CONF_MAX_SHIPMENTS,
    CONF_PASSWORD,
    CONF_PRIORITIZE_UNDELIVERED,
    CONF_STALE_SHIPMENT_DAY_LIMIT,
    CONF_TOKENS,
    CONF_USERNAME,
    DEFAULT_COMPLETED_SHIPMENT_DAYS_SHOWN,
    DEFAULT_INCLUDE_PICKUP_DETAILS,
    DEFAULT_MAX_SHIPMENTS,
    DEFAULT_PRIORITIZE_UNDELIVERED,
    DEFAULT_STALE_SHIPMENT_DAY_LIMIT,
    DOMAIN,
    LANGUAGES,
)
from .exceptions import PostiAuthError, PostiError
from .login import log_in

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password"))
DAYS_SELECTOR = NumberSelector(NumberSelectorConfig(min=0, max=365, step=1, mode=NumberSelectorMode.BOX))

SETTING_FIELDS = {
    vol.Required(CONF_LANGUAGE): SelectSelector(
        SelectSelectorConfig(options=LANGUAGES, translation_key=CONF_LANGUAGE, mode=SelectSelectorMode.DROPDOWN)
    ),
    vol.Required(CONF_PRIORITIZE_UNDELIVERED): BooleanSelector(),
    vol.Required(CONF_MAX_SHIPMENTS): NumberSelector(
        NumberSelectorConfig(min=1, max=50, step=1, mode=NumberSelectorMode.BOX)
    ),
    vol.Required(CONF_STALE_SHIPMENT_DAY_LIMIT): DAYS_SELECTOR,
    vol.Required(CONF_COMPLETED_SHIPMENT_DAYS_SHOWN): DAYS_SELECTOR,
    vol.Required(CONF_INCLUDE_PICKUP_DETAILS): BooleanSelector(),
}

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        **SETTING_FIELDS,
    }
)
# The saved password isn't shown; an empty field keeps it.
RECONFIGURE_SCHEMA = vol.Schema({vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR, **SETTING_FIELDS})
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR})


def clean_settings(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Submitted settings, normalised for storing."""
    data = {
        key: user_input[key]
        for key in (CONF_LANGUAGE, CONF_PRIORITIZE_UNDELIVERED, CONF_INCLUDE_PICKUP_DETAILS)
        if key in user_input
    }
    for key in (CONF_MAX_SHIPMENTS, CONF_STALE_SHIPMENT_DAY_LIMIT, CONF_COMPLETED_SHIPMENT_DAYS_SHOWN):
        if key in user_input:
            data[key] = int(user_input[key])
    return data


async def try_login(hass: HomeAssistant, username: str, password: str) -> tuple[dict[str, str], dict[str, Any]]:
    """Logs in. Returns the errors, and the tokens when the login worked."""
    try:
        tokens = await hass.async_add_executor_job(log_in, username, password)
    except PostiAuthError:
        return {"base": "invalid_auth"}, {}
    except PostiError:
        return {"base": "cannot_connect"}, {}
    return {}, tokens


class PostiConfigFlow(ConfigFlow, domain=DOMAIN):
    # 1.1 entries have no unique id; __init__.async_migrate_entry gives them one.
    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = str(user_input[CONF_USERNAME]).strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()
            errors, tokens = await try_login(self.hass, username, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        **clean_settings(user_input),
                        CONF_TOKENS: tokens,
                    },
                )

        values = {**user_input, CONF_PASSWORD: ""} if user_input else self._defaults()
        return self.async_show_form(
            step_id="user", data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, values), errors=errors
        )

    def _defaults(self) -> dict[str, Any]:
        language = self.hass.config.language[:2]
        return {
            CONF_LANGUAGE: language if language in LANGUAGES else "en",
            CONF_PRIORITIZE_UNDELIVERED: DEFAULT_PRIORITIZE_UNDELIVERED,
            CONF_MAX_SHIPMENTS: DEFAULT_MAX_SHIPMENTS,
            CONF_STALE_SHIPMENT_DAY_LIMIT: DEFAULT_STALE_SHIPMENT_DAY_LIMIT,
            CONF_COMPLETED_SHIPMENT_DAYS_SHOWN: DEFAULT_COMPLETED_SHIPMENT_DAYS_SHOWN,
            CONF_INCLUDE_PICKUP_DETAILS: DEFAULT_INCLUDE_PICKUP_DETAILS,
        }

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The password again, when Posti no longer accepts the saved one."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, tokens = await try_login(self.hass, entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD], CONF_TOKENS: tokens}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Changing the settings or password of an account."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **clean_settings(user_input)}
            if password := user_input.get(CONF_PASSWORD):
                errors, tokens = await try_login(self.hass, entry.data[CONF_USERNAME], password)
                data |= {CONF_PASSWORD: password, CONF_TOKENS: tokens}
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)

        values = {key: value for key, value in (user_input or entry.data).items() if key != CONF_PASSWORD}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(RECONFIGURE_SCHEMA, values),
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )
