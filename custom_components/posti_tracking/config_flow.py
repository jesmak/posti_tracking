"""Config flow: an account is added by logging in to OmaPosti with strong authentication.

The user picks an authentication method, authenticates in their own browser,
and pastes back the address the browser lands on. The integration then
registers itself as a trusted device of the account, and saves the device and
the tokens with the entry; the coordinator renews the tokens with the device
key later. When Posti no longer trusts the device, or for entries of earlier
versions, which logged in with a password, reauthentication takes the same
steps. Settings are changed by reconfiguring.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
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

from .api import query_shipments, shipments_in
from .const import (
    CONF_ADDRESS,
    CONF_COMPLETED_SHIPMENT_DAYS_SHOWN,
    CONF_DEVICE,
    CONF_INCLUDE_PICKUP_DETAILS,
    CONF_LANGUAGE,
    CONF_MAX_SHIPMENTS,
    CONF_METHOD,
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
    METHOD_ORDER,
    UAS_HOST,
)
from .device import Device, deactivate, jwt_payload, provision, refresh
from .exceptions import InvalidReturnAddress, PostiAuthError, PostiError
from .login import StrongLogin, bank_address, finish_login, start_login

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

SETTINGS_SCHEMA = vol.Schema(SETTING_FIELDS)
ADDRESS_SCHEMA = vol.Schema({vol.Required(CONF_ADDRESS): TextSelector(TextSelectorConfig(type=TextSelectorType.URL))})
# Home Assistant doesn't allow addresses in translations, so they come in as placeholders.
RETURN_ADDRESS_EXAMPLE = f"https://{UAS_HOST}/uas/return/…"
SECURITY_URL = "https://github.com/jesmak/posti_tracking#security"
METHOD_ID = re.compile(r"telia\.ftn\.oidc\.([a-z0-9_]+)\.1")


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


def method_choices(methods: Iterable[str]) -> dict[str, str]:
    """The authentication methods as dropdown choices, in the order shown, with the method id of each.

    A choice is the method's provider (op, nordea), which names its translation; ids of another shape are kept as
    they are, cleaned to work as a translation key.
    """
    choices: dict[str, str] = {}
    for method in methods:
        match = METHOD_ID.fullmatch(method)
        choices[match.group(1) if match else re.sub(r"[^a-z0-9_]", "_", method.lower())] = method
    order = {choice: index for index, choice in enumerate(METHOD_ORDER)}
    return dict(sorted(choices.items(), key=lambda item: order.get(item[0], len(order))))


def account_email(tokens: Mapping[str, Any]) -> str | None:
    """The account's email address, from the id token of its login."""
    emails = (jwt_payload(tokens.get("id_token")) or {}).get("email")
    for email in emails if isinstance(emails, list) else [emails]:
        if isinstance(email, str) and "@" in email:
            return email.strip()
    return None


class PostiConfigFlow(ConfigFlow, domain=DOMAIN):
    # 1.1 entries have no unique id, and 1.2 entries a password; __init__.async_migrate_entry fixes both.
    VERSION = 1
    MINOR_VERSION = 3

    def __init__(self) -> None:
        # One login's HTTP session: its cookies must last from choosing the method to finishing the login.
        self._session: aiohttp.ClientSession | None = None
        self._login: StrongLogin | None = None
        self._choices: dict[str, str] = {}
        self._bank_address = ""
        self._account: dict[str, Any] = {}

    @callback
    def async_remove(self) -> None:
        self._end_session()

    def _end_session(self) -> None:
        # Detached, not closed: the session shares Home Assistant's connection pool.
        if self._session is not None:
            self._session.detach()
            self._session = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The authentication method."""
        return await self._choose_method(user_input)

    async def _choose_method(self, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        if user_input is None or self._login is None:
            return await self._show_methods()
        try:
            self._bank_address = await bank_address(self._http(), self._login, self._choices[user_input[CONF_METHOD]])
        except PostiError:
            return await self._show_methods({"base": "cannot_connect"})
        return await self.async_step_authenticate()

    async def _show_methods(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        """Starts a new login, and shows the authentication methods Posti offers for it."""
        self._end_session()
        try:
            self._login = await start_login(self._http())
        except PostiError:
            return self.async_abort(reason="cannot_connect")
        self._choices = method_choices(self._login.methods)
        schema = vol.Schema(
            {
                vol.Required(CONF_METHOD): SelectSelector(
                    SelectSelectorConfig(
                        options=list(self._choices), translation_key=CONF_METHOD, mode=SelectSelectorMode.DROPDOWN
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors or {})

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = async_create_clientsession(self.hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar())
        return self._session

    async def async_step_authenticate(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Authenticating in the user's own browser, and the address it lands on."""
        errors: dict[str, str] = {}
        if user_input is not None and self._login is not None:
            try:
                tokens = await finish_login(self._http(), self._login, user_input[CONF_ADDRESS])
            except InvalidReturnAddress:
                errors["base"] = "invalid_address"
            except PostiAuthError:
                # The login can't be finished any more, so the user starts over.
                return await self._show_methods({"base": "invalid_auth"})
            except PostiError:
                return await self._show_methods({"base": "cannot_connect"})
            else:
                return await self._register_device(tokens)

        return self.async_show_form(
            step_id="authenticate",
            data_schema=ADDRESS_SCHEMA,
            errors=errors,
            description_placeholders={
                "url": self._bank_address,
                "return_address": RETURN_ADDRESS_EXAMPLE,
                "security_url": SECURITY_URL,
            },
        )

    async def _register_device(self, tokens: dict[str, Any]) -> ConfigFlowResult:
        """Registers the integration as a device of the account, and checks that the device renews the tokens."""
        email = account_email(tokens)
        if email is None:
            return await self._show_methods({"base": "no_account"})
        if self.source == SOURCE_REAUTH:
            # Checked before registering a device, so that another account isn't left with one.
            if email.lower() != self._get_reauth_entry().unique_id:
                return self.async_abort(reason="wrong_account")
        else:
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

        session = self._http()
        try:
            device, tokens = await provision(session, tokens)
            tokens = {**tokens, **await refresh(session, device, tokens["id_token"])}
            shipments_in(*await query_shipments(session, tokens))
            if self.source == SOURCE_REAUTH and CONF_DEVICE in self._get_reauth_entry().data:
                old_device = Device.from_data(self._get_reauth_entry().data[CONF_DEVICE])
                try:
                    await deactivate(session, device, old_device.device_id, tokens["id_token"])
                except PostiError:
                    # Usually it is gone already: Posti no longer trusting it is what starts reauthentication.
                    pass
        except PostiError:
            # A login registers only one device, so trying again needs a new login.
            return await self._show_methods({"base": "device_failed"})
        finally:
            self._end_session()

        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            data = {key: value for key, value in entry.data.items() if key != CONF_PASSWORD}
            return self.async_update_reload_and_abort(
                entry, data={**data, CONF_DEVICE: device.as_data(), CONF_TOKENS: tokens}
            )
        self._account = {"title": email, CONF_DEVICE: device.as_data(), CONF_TOKENS: tokens}
        return await self.async_step_settings()

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=self._account["title"],
                data={
                    # The sensor and event entities are named after it, as in earlier versions.
                    CONF_USERNAME: self._account["title"],
                    **clean_settings(user_input),
                    CONF_DEVICE: self._account[CONF_DEVICE],
                    CONF_TOKENS: self._account[CONF_TOKENS],
                },
            )
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(SETTINGS_SCHEMA, self._defaults()),
            description_placeholders={"account": self._account["title"]},
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
        """Logging in again takes the same steps as adding an account.

        Home Assistant starts this flow as soon as the setup fails, so the login only starts once the user is there
        to finish it: Posti's login doesn't last long.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm", description_placeholders={"username": self._get_reauth_entry().title}
            )
        return await self._show_methods()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Changing the settings of an account."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_update_reload_and_abort(entry, data={**entry.data, **clean_settings(user_input)})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(SETTINGS_SCHEMA, dict(entry.data)),
            description_placeholders={"username": entry.title},
        )
