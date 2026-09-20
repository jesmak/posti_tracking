"""Adding an account, a new password, and changing settings."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.posti_tracking.const import DOMAIN
from custom_components.posti_tracking.exceptions import PostiAuthError, PostiError

from .conftest import ENTRY_DATA, EXPIRED_TOKENS, PASSWORD, USERNAME, VALID_TOKENS

FORM = {
    "username": f" {USERNAME} ",
    "password": PASSWORD,
    "language": "fi",
    "prioritize_undelivered": True,
    "max_shipments": 5.0,
    "stale_shipment_day_limit": 15.0,
    "completed_shipment_day_shown": 3.0,
    "include_pickup_details": False,
}


def account(hass: HomeAssistant, **data: object) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title=USERNAME, version=1, minor_version=2, unique_id=USERNAME, data={**ENTRY_DATA, **data}
    )
    entry.add_to_hass(hass)
    return entry


async def submit(hass: HomeAssistant, form: dict) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], form)


async def test_adding_an_account(hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any) -> None:
    result = await submit(hass, FORM)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USERNAME
    assert result["data"] == ENTRY_DATA, "the tokens of the login are saved with the settings"
    assert result["result"].unique_id == USERNAME
    login.assert_called_once_with(USERNAME, PASSWORD)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.posti_matti_meikalainen_example_com") is not None
    assert login.call_count == 1, "setting up uses the saved tokens"


async def test_a_wrong_password(hass: HomeAssistant, login: Any) -> None:
    login.side_effect = PostiAuthError("wrong")
    result = await submit(hass, FORM)
    assert result["errors"] == {"base": "invalid_auth"}
    assert result["data_schema"].schema, "the form is shown again"


async def test_posti_cannot_be_reached(hass: HomeAssistant, login: Any) -> None:
    login.side_effect = PostiError("down")
    result = await submit(hass, FORM)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_an_account_is_added_only_once(hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any) -> None:
    account(hass)
    result = await submit(hass, {**FORM, "username": USERNAME.upper()})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    login.assert_not_called()


async def test_a_new_password(hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any) -> None:
    entry = account(hass, password="old", tokens=EXPIRED_TOKENS)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "new"})
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    login.assert_any_call(USERNAME, "new")
    assert entry.data == {**ENTRY_DATA, "password": "new", "tokens": VALID_TOKENS}


async def test_changing_settings_keeps_the_password(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any
) -> None:
    entry = account(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert "password" not in {str(key): key.description for key in result["data_schema"].schema if key.description}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "language": "en",
            "prioritize_undelivered": False,
            "max_shipments": 10.0,
            "stale_shipment_day_limit": 30.0,
            "completed_shipment_day_shown": 1.0,
            "include_pickup_details": False,
        },
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    login.assert_not_called()
    assert entry.data == {
        **ENTRY_DATA,
        "language": "en",
        "prioritize_undelivered": False,
        "max_shipments": 10,
        "stale_shipment_day_limit": 30,
        "completed_shipment_day_shown": 1,
        "include_pickup_details": False,
    }


async def test_changing_the_password(hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any) -> None:
    entry = account(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "password": "new",
            "language": "fi",
            "prioritize_undelivered": True,
            "max_shipments": 5.0,
            "stale_shipment_day_limit": 15.0,
            "completed_shipment_day_shown": 3.0,
            "include_pickup_details": False,
        },
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    login.assert_called_once_with(USERNAME, "new")
    assert entry.data["password"] == "new"
