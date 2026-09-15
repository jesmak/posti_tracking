"""Setting up an account and its sensor, entries of earlier versions, and login problems."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.posti_tracking.const import DOMAIN, GRAPH_API_URL
from custom_components.posti_tracking.exceptions import PostiAuthError
from custom_components.posti_tracking.sensor import PostiSensor

from .conftest import ENTRY_DATA, EXPIRED_TOKENS, NOW, SHIPMENTS, TOKEN_URL, USERNAME, VALID_TOKENS, answers

ENTITY_ID = "sensor.posti_matti_meikalainen_example_com"


def account(
    hass: HomeAssistant, *, minor_version: int = 2, unique_id: str | None = USERNAME, **data: object
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=USERNAME,
        version=1,
        minor_version=minor_version,
        unique_id=unique_id,
        data={**ENTRY_DATA, **data},
    )
    entry.add_to_hass(hass)
    return entry


async def test_the_sensor_lists_the_packages(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any, freezer: FrozenDateTimeFactory
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
    login.assert_not_called()

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_packages_are_kept_out_of_the_recorder() -> None:
    assert "packages" in PostiSensor._unrecorded_attributes


async def test_an_entry_of_an_earlier_version_gets_a_unique_id(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any
) -> None:
    entry = account(hass, minor_version=1, unique_id=None, username="Matti.Meikalainen@example.com")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert (entry.minor_version, entry.unique_id) == (2, USERNAME)


async def test_tokens_of_a_new_login_are_saved(hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any) -> None:
    """Expired tokens that Posti won't renew lead to a new login, whose tokens are saved."""
    omaposti.post(TOKEN_URL, status=400, json={"error": "invalid_grant"})
    entry = account(hass, tokens=EXPIRED_TOKENS)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    login.assert_called_once()
    assert entry.data["tokens"] == VALID_TOKENS
    assert entry.state is ConfigEntryState.LOADED


async def test_a_password_posti_no_longer_accepts_asks_for_it_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any
) -> None:
    login.side_effect = PostiAuthError("wrong password")
    aioclient_mock.post(GRAPH_API_URL, status=401)
    entry = account(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == SOURCE_REAUTH


async def test_the_sensor_is_unavailable_while_posti_is_down(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(NOW)
    aioclient_mock.post(GRAPH_API_URL, side_effect=answers((200, {"data": {"shipment": SHIPMENTS}}), (503, None)))
    entry = account(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state != STATE_UNAVAILABLE

    freezer.tick(timedelta(minutes=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE
