"""Setting up an account and its sensor, entries of earlier versions, and login problems."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.posti_tracking.const import DOMAIN, GRAPH_API_URL
from custom_components.posti_tracking.device import Device
from custom_components.posti_tracking.exceptions import PostiAuthError, PostiDeviceGone, PostiError

from .conftest import ENTRY_DATA, EXPIRED_TOKENS, NOW, PASSWORD_ENTRY_DATA, SHIPMENTS, USERNAME, VALID_TOKENS, answers, page_of

ENTITY_ID = "sensor.posti_matti_meikalainen_example_com"


def account(
    hass: HomeAssistant,
    *,
    minor_version: int = 5,
    unique_id: str | None = USERNAME,
    data: dict[str, Any] = ENTRY_DATA,
    **changes: object,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        version=1,
        minor_version=minor_version,
        unique_id=unique_id,
        data={**data, **changes},
    )
    entry.add_to_hass(hass)
    return entry


async def test_the_sensor_lists_the_packages(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, renewal: Any, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW)
    entry = account(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == "2026-09-16T05:30:00+00:00"
    assert state.attributes["device_class"] == "timestamp"
    assert state.attributes["attribution"] == "Data provided by Posti Group Oyj"
    assert [package["shipment_number"] for package in state.attributes["packages"]] == [
        "JJFI0002",
        "JJFI0008",
        "JJFI0001",
        "JJFI0004",
        "JJFI0003",
    ]
    assert er.async_get(hass).async_get(ENTITY_ID).unique_id == f"posti_{USERNAME}", "as in earlier versions"
    renewal.assert_not_called()

    assert await hass.config_entries.async_unload(entry.entry_id)


# An entry from before 1.4, with the switch that held back both the pickup point and the code.
OLD_ENTRY_DATA = {key: value for key, value in PASSWORD_ENTRY_DATA.items() if key != "pickup_code"} | {"include_pickup_details": False}


async def test_an_entry_of_1_x_gets_a_unique_id(hass: HomeAssistant, omaposti: AiohttpClientMocker, renewal: Any) -> None:
    entry = account(hass, minor_version=1, unique_id=None, data=OLD_ENTRY_DATA | {"username": "Matti.Meikalainen@example.com"})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert (entry.minor_version, entry.unique_id) == (5, USERNAME)
    assert "password" not in entry.data


async def test_the_password_of_an_entry_of_2_x_is_dropped(hass: HomeAssistant, omaposti: AiohttpClientMocker, renewal: Any) -> None:
    entry = account(hass, minor_version=2, data=OLD_ENTRY_DATA)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 5
    assert "password" not in entry.data


@pytest.mark.parametrize(("switch", "choice"), [(True, "code"), (False, "none"), (None, "none")])
async def test_the_pickup_switch_of_an_entry_of_3_0_becomes_a_choice(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, renewal: Any, switch: bool | None, choice: str
) -> None:
    data = {key: value for key, value in ENTRY_DATA.items() if key != "pickup_code"}
    if switch is not None:
        data["include_pickup_details"] = switch
    entry = account(hass, minor_version=3, data=data)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 5
    assert entry.data["pickup_code"] == choice
    assert "include_pickup_details" not in entry.data


async def test_an_entry_of_3_1_gets_its_maximum_for_sent_packages_too(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, renewal: Any
) -> None:
    data = {key: value for key, value in ENTRY_DATA.items() if key != "max_outgoing_shipments"} | {"max_shipments": 8}
    entry = account(hass, minor_version=4, data=data)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 5
    assert entry.data["max_shipments"] == 8
    assert entry.data["max_outgoing_shipments"] == 8


async def test_a_password_entry_asks_for_a_new_login(hass: HomeAssistant) -> None:
    entry = account(hass, minor_version=2, data=OLD_ENTRY_DATA)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == SOURCE_REAUTH


async def test_renewed_tokens_are_saved(hass: HomeAssistant, omaposti: AiohttpClientMocker, renewal: Any) -> None:
    entry = account(hass, tokens=EXPIRED_TOKENS)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    renewal.assert_called_once()
    assert entry.data["tokens"] == {**EXPIRED_TOKENS, **renewal.return_value}
    assert entry.data["tokens"]["id_token"] == VALID_TOKENS["id_token"]
    assert entry.state is ConfigEntryState.LOADED


async def test_a_device_posti_no_longer_trusts_asks_for_a_new_login(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any
) -> None:
    renewal.side_effect = PostiAuthError("deactivated")
    entry = account(hass, tokens=EXPIRED_TOKENS)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == SOURCE_REAUTH


async def test_the_sensor_is_unavailable_while_posti_is_down(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW)
    aioclient_mock.post(GRAPH_API_URL, side_effect=answers((200, page_of(SHIPMENTS)), (503, None)))
    entry = account(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state != STATE_UNAVAILABLE

    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


async def test_removing_an_account_deactivates_its_device_with_a_fresh_login(hass: HomeAssistant) -> None:
    entry = account(hass)
    with (
        patch("custom_components.posti_tracking.refresh", return_value={"id_token": "fresh"}) as refresh,
        patch("custom_components.posti_tracking.deactivate") as deactivate,
    ):
        await hass.config_entries.async_remove(entry.entry_id)

    device = Device.from_data(ENTRY_DATA["device"])
    assert refresh.call_args.args[1:] == (device, VALID_TOKENS["id_token"])
    assert deactivate.call_args.args[1:] == (device, device.device_id, "fresh")


async def test_removing_falls_back_to_the_saved_login_when_it_cannot_be_renewed(hass: HomeAssistant) -> None:
    entry = account(hass)
    with (
        patch("custom_components.posti_tracking.refresh", side_effect=PostiError("down")),
        patch("custom_components.posti_tracking.deactivate") as deactivate,
    ):
        await hass.config_entries.async_remove(entry.entry_id)

    assert deactivate.call_args.args[3] == VALID_TOKENS["id_token"]


async def test_a_device_posti_no_longer_trusts_needs_no_deactivation(hass: HomeAssistant) -> None:
    entry = account(hass)
    with (
        patch("custom_components.posti_tracking.refresh", side_effect=PostiDeviceGone("gone")),
        patch("custom_components.posti_tracking.deactivate") as deactivate,
    ):
        await hass.config_entries.async_remove(entry.entry_id)

    deactivate.assert_not_called()


async def test_a_login_too_old_to_renew_still_deactivates_the_device(hass: HomeAssistant) -> None:
    # Refused to renew is not the same as gone: the device may still be trusted, so it is deactivated with the saved login.
    entry = account(hass)
    with (
        patch("custom_components.posti_tracking.refresh", side_effect=PostiAuthError("invalid_grant")),
        patch("custom_components.posti_tracking.deactivate") as deactivate,
    ):
        await hass.config_entries.async_remove(entry.entry_id)

    assert deactivate.call_args.args[3] == VALID_TOKENS["id_token"]


async def test_a_device_that_cannot_be_deactivated_is_left_to_the_user(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry = account(hass)
    with (
        patch("custom_components.posti_tracking.refresh", return_value={"id_token": "fresh"}),
        patch("custom_components.posti_tracking.deactivate", side_effect=PostiError("Posti couldn't be reached")),
    ):
        await hass.config_entries.async_remove(entry.entry_id)

    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert "Remove it in the OmaPosti app under More, Settings, Security settings" in caplog.text


async def test_removing_a_password_entry_has_no_device_to_deactivate(hass: HomeAssistant) -> None:
    entry = account(hass, data=PASSWORD_ENTRY_DATA)
    with patch("custom_components.posti_tracking.deactivate") as deactivate:
        await hass.config_entries.async_remove(entry.entry_id)

    deactivate.assert_not_called()
