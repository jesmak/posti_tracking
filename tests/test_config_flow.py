"""Adding an account with strong authentication, logging in again, and changing settings."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.posti_tracking.const import DOMAIN
from custom_components.posti_tracking.device import Device
from custom_components.posti_tracking.exceptions import InvalidReturnAddress, PostiAuthError, PostiError
from custom_components.posti_tracking.login import StrongLogin

from .conftest import ENTRY_DATA, EXPIRED_TOKENS, PASSWORD_ENTRY_DATA, USERNAME, VALID_TOKENS, jwt

SETTINGS = {
    "language": "fi",
    "prioritize_undelivered": True,
    "max_shipments": 5.0,
    "max_outgoing_shipments": 5.0,
    "stale_shipment_day_limit": 15.0,
    "completed_shipment_day_shown": 3.0,
    "pickup_code": "none",
}
METHODS = {
    f"telia.ftn.oidc.{provider}.1": f"/uas/authn/session-1/select?method=telia.ftn.oidc.{provider}.1"
    for provider in ("nordea", "newbank", "op", "mobileid", "hst")
}
BANK_ADDRESS = "https://tunnistus.telia.fi/uas/oauth2/authorization?request=eyJ.request"
RETURN_ADDRESS = "https://todentaminen.posti.fi/uas/return/telia.ftn.oidc.op.1/redirect?code=bank-code&state=s1"
LOGIN_TOKENS = {**VALID_TOKENS, "id_token": jwt(datetime(2100, 1, 1, tzinfo=UTC), sub="account-1", email=[USERNAME])}
DEVICE = Device("device-1", "private key", "c2FsdA==", "account-1")
RENEWED = {"id_token": "id-2", "access_token": "access-2", "role_tokens": VALID_TOKENS["role_tokens"]}


def account(hass: HomeAssistant, data: dict[str, Any] = ENTRY_DATA) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=USERNAME, version=1, minor_version=5, unique_id=USERNAME, data=data)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def strong_login() -> Iterator[dict[str, Any]]:
    """Posti's login and the device registration, replaced: everything works unless a test tells it otherwise."""
    flow = "custom_components.posti_tracking.config_flow"
    with (
        patch(f"{flow}.start_login", return_value=StrongLogin("verifier", METHODS)) as start,
        patch(f"{flow}.bank_address", return_value=BANK_ADDRESS) as bank,
        patch(f"{flow}.finish_login", return_value=LOGIN_TOKENS) as finish,
        patch(f"{flow}.provision", return_value=(DEVICE, LOGIN_TOKENS)) as provision,
        patch(f"{flow}.refresh", return_value=RENEWED) as refresh,
        patch(f"{flow}.deactivate") as deactivate,
        patch("custom_components.posti_tracking.async_setup_entry", return_value=True),
    ):
        yield {
            "start": start,
            "bank": bank,
            "finish": finish,
            "provision": provision,
            "refresh": refresh,
            "deactivate": deactivate,
        }


async def to_authentication(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"method": "op"})


async def test_adding_an_account(hass: HomeAssistant, omaposti: AiohttpClientMocker, strong_login: dict[str, Any]) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    [field] = result["data_schema"].schema
    # Mobiilivarmenne first, then the known methods in their order, then the ones Posti added later.
    assert result["data_schema"].schema[field].config["options"] == ["mobileid", "nordea", "op", "hst", "newbank"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"method": "op"})
    assert result["step_id"] == "authenticate"
    assert result["description_placeholders"]["url"] == BANK_ADDRESS
    assert strong_login["bank"].call_args.args[2] == "telia.ftn.oidc.op.1"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": RETURN_ADDRESS})
    assert result["step_id"] == "settings"
    assert result["description_placeholders"] == {"account": USERNAME}
    assert strong_login["finish"].call_args.args[2] == RETURN_ADDRESS
    # Nothing is registered before the settings are in.
    strong_login["provision"].assert_not_called()

    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    # The renewed tokens are tried on OmaPosti before anything is saved.
    [(_, _, _, headers)] = omaposti.mock_calls
    assert headers["x-posti-token"] == "Bearer id-2"
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USERNAME
    assert result["result"].unique_id == USERNAME
    assert result["data"] == {
        "username": USERNAME,
        "language": "fi",
        "prioritize_undelivered": True,
        "max_shipments": 5,
        "max_outgoing_shipments": 5,
        "stale_shipment_day_limit": 15,
        "completed_shipment_day_shown": 3,
        "pickup_code": "none",
        "device": DEVICE.as_data(),
        "tokens": {**LOGIN_TOKENS, **RENEWED},
    }


async def test_an_address_that_is_not_the_return_address(hass: HomeAssistant, strong_login: dict[str, Any]) -> None:
    strong_login["finish"].side_effect = InvalidReturnAddress("wrong")
    result = await to_authentication(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": "https://example.com/"})
    assert result["step_id"] == "authenticate", "the same login can still be finished"
    assert result["errors"] == {"base": "invalid_address"}
    assert strong_login["start"].call_count == 1


async def test_an_authentication_posti_refuses(hass: HomeAssistant, strong_login: dict[str, Any]) -> None:
    strong_login["finish"].side_effect = PostiAuthError("refused")
    result = await to_authentication(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": RETURN_ADDRESS})
    assert result["step_id"] == "user", "a new login starts"
    assert result["errors"] == {"base": "invalid_auth"}
    assert strong_login["start"].call_count == 2


async def test_a_device_posti_refuses(hass: HomeAssistant, strong_login: dict[str, Any]) -> None:
    strong_login["provision"].side_effect = PostiError("refused")
    result = await to_authentication(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": RETURN_ADDRESS})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "device_failed"}
    # No device was registered, so there is none to remove.
    strong_login["deactivate"].assert_not_called()


async def test_a_device_that_does_not_work_is_removed_again(hass: HomeAssistant, strong_login: dict[str, Any]) -> None:
    strong_login["refresh"].side_effect = PostiError("the vault didn't open")
    result = await to_authentication(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": RETURN_ADDRESS})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    assert result["errors"] == {"base": "device_failed"}
    assert strong_login["deactivate"].call_args.args[1:3] == (DEVICE, DEVICE.device_id)


async def test_closing_the_settings_leaves_no_device_behind(hass: HomeAssistant, strong_login: dict[str, Any]) -> None:
    result = await to_authentication(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": RETURN_ADDRESS})
    assert result["step_id"] == "settings"

    hass.config_entries.flow.async_abort(result["flow_id"])
    strong_login["provision"].assert_not_called()


async def test_posti_cannot_be_reached(hass: HomeAssistant, strong_login: dict[str, Any]) -> None:
    strong_login["start"].side_effect = PostiError("down")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_an_account_is_added_only_once(hass: HomeAssistant, strong_login: dict[str, Any]) -> None:
    account(hass)
    strong_login["finish"].return_value = {
        **LOGIN_TOKENS,
        "id_token": jwt(datetime(2100, 1, 1, tzinfo=UTC), email=[USERNAME.upper()]),
    }
    result = await to_authentication(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": RETURN_ADDRESS})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    strong_login["provision"].assert_not_called()


async def test_logging_in_again_replaces_the_password(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, strong_login: dict[str, Any]
) -> None:
    entry = account(hass, {**PASSWORD_ENTRY_DATA, "tokens": EXPIRED_TOKENS})

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["username"] == USERNAME
    strong_login["start"].assert_not_called()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"method": "op"})
    assert result["step_id"] == "authenticate"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": RETURN_ADDRESS})
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    password_free = {key: value for key, value in PASSWORD_ENTRY_DATA.items() if key not in ("password", "tokens")}
    assert entry.data == {**password_free, "device": DEVICE.as_data(), "tokens": {**LOGIN_TOKENS, **RENEWED}}
    strong_login["deactivate"].assert_not_called()


@pytest.mark.parametrize("deactivated", [None, PostiError("not found")])
async def test_logging_in_again_deactivates_the_old_device(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, strong_login: dict[str, Any], deactivated: Exception | None
) -> None:
    entry = account(hass)
    old_device = Device.from_data(ENTRY_DATA["device"])
    strong_login["deactivate"].side_effect = deactivated

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"method": "op"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": RETURN_ADDRESS})
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    # The new device removes the old one, never the account's trusted devices altogether.
    assert strong_login["deactivate"].call_args.args[1:] == (DEVICE, old_device.device_id, RENEWED["id_token"])
    assert entry.data["device"] == DEVICE.as_data()


async def test_logging_in_again_with_another_account(hass: HomeAssistant, strong_login: dict[str, Any]) -> None:
    entry = account(hass)
    strong_login["finish"].return_value = {
        **LOGIN_TOKENS,
        "id_token": jwt(datetime(2100, 1, 1, tzinfo=UTC), sub="account-2", email=["maija.meikalainen@example.com"]),
    }

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"method": "op"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": RETURN_ADDRESS})

    assert result["reason"] == "wrong_account"
    strong_login["provision"].assert_not_called()
    assert entry.data == ENTRY_DATA


async def test_logging_in_again_after_the_email_has_changed(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, strong_login: dict[str, Any]
) -> None:
    # The same account, by its subject, though its email is no longer the one the entry was set up with.
    entry = account(hass)
    strong_login["finish"].return_value = {
        **LOGIN_TOKENS,
        "id_token": jwt(datetime(2100, 1, 1, tzinfo=UTC), sub="account-1", email=["uusi.osoite@example.com"]),
    }

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"method": "op"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": RETURN_ADDRESS})
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"


async def test_changing_settings(hass: HomeAssistant, strong_login: dict[str, Any]) -> None:
    entry = account(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["description_placeholders"] == {"username": USERNAME}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "language": "en",
            "prioritize_undelivered": False,
            "max_shipments": 10.0,
            "max_outgoing_shipments": 0.0,
            "stale_shipment_day_limit": 30.0,
            "completed_shipment_day_shown": 1.0,
            "pickup_code": "code_and_qr",
        },
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    strong_login["start"].assert_not_called()
    assert entry.data == {
        **ENTRY_DATA,
        "language": "en",
        "prioritize_undelivered": False,
        "max_shipments": 10,
        "max_outgoing_shipments": 0,
        "stale_shipment_day_limit": 30,
        "completed_shipment_day_shown": 1,
        "pickup_code": "code_and_qr",
    }
