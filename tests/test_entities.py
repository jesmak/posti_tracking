"""The counts of an account's packages, and the events its packages fire."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse

from custom_components.posti_tracking.const import DOMAIN, GRAPH_API_URL

from .conftest import ENTRY_DATA, NOW, USERNAME, event, shipment

EVENT_ENTITY = "event.posti_matti_meikalainen_example_com_package"
ON_THE_WAY = "sensor.posti_matti_meikalainen_example_com_packages_on_the_way"
READY = "sensor.posti_matti_meikalainen_example_com_packages_ready_for_pickup"


async def answer(shipments: list[dict[str, Any]]) -> AiohttpClientMockResponse:
    """OmaPosti's answer, read afresh each time so a test can change the shipments."""
    return AiohttpClientMockResponse("post", GRAPH_API_URL, json={"data": {"shipment": list(shipments)}})


def account(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title=USERNAME, version=1, minor_version=2, unique_id=USERNAME, data=ENTRY_DATA
    )
    entry.add_to_hass(hass)
    return entry


async def set_up(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> MockConfigEntry:
    freezer.move_to(NOW)
    entry = account(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_the_counts_of_the_packages(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)

    # Of the packages shown: two are on their way, one of them ready for pickup.
    assert hass.states.get(ON_THE_WAY).state == "3"
    assert hass.states.get(ON_THE_WAY).attributes["state_class"] == "measurement"
    assert hass.states.get(READY).state == "1"
    registry = er.async_get(hass)
    assert registry.async_get(ON_THE_WAY).unique_id == f"posti_{USERNAME}_on_the_way"
    assert registry.async_get(READY).unique_id == f"posti_{USERNAME}_ready_for_pickup"


async def test_the_packages_already_there_are_not_events(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)

    state = hass.states.get(EVENT_ENTITY)
    assert state.state == "unknown", "what was already there when Home Assistant started has not just happened"
    assert set(state.attributes["event_types"]) == {
        "new_package",
        "moved",
        "ready_for_pickup",
        "delivered",
        "returned",
    }


async def test_a_package_becoming_ready_for_pickup_fires_an_event(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any, freezer: FrozenDateTimeFactory
) -> None:
    moving = shipment("JJFI0100", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport"))
    arrived = shipment(
        "JJFI0100",
        "READY_FOR_PICKUP",
        event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport"),
        event("2026-09-16T09:30:00Z", "Noudettavissa", "Ready for pickup"),
    )
    shipments = [moving]
    aioclient_mock.post(GRAPH_API_URL, side_effect=lambda *_: answer(shipments))

    await set_up(hass, freezer)
    assert hass.states.get(EVENT_ENTITY).state == "unknown"

    fired: list[State] = []
    hass.bus.async_listen(
        "state_changed",
        lambda call: fired.append(call.data["new_state"]) if call.data["entity_id"] == EVENT_ENTITY else None,
    )

    shipments[:] = [arrived]
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(EVENT_ENTITY).attributes["event_type"] == "ready_for_pickup"
    assert hass.states.get(EVENT_ENTITY).attributes["shipment_number"] == "JJFI0100"
    assert hass.states.get(EVENT_ENTITY).attributes["destination"] == "K-Market Keskusta, Pakettiautomaatti"
    assert hass.states.get(READY).state == "1"
    assert [state.attributes.get("event_type") for state in fired if state] == ["ready_for_pickup"]


async def test_every_change_of_one_update_fires_its_own_event(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any, freezer: FrozenDateTimeFactory
) -> None:
    before = [
        shipment("JJFI0201", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport")),
        shipment("JJFI0202", "IN_DELIVERY", event("2026-09-16T05:40:00Z", "Jakelussa", "In delivery")),
    ]
    after = [
        shipment(
            "JJFI0201",
            "READY_FOR_PICKUP",
            event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport"),
            event("2026-09-16T09:30:00Z", "Noudettavissa", "Ready for pickup"),
        ),
        shipment(
            "JJFI0202",
            "DELIVERED",
            event("2026-09-16T05:40:00Z", "Jakelussa", "In delivery"),
            event("2026-09-16T09:31:00Z", "Toimitettu", "Delivered"),
        ),
    ]
    shipments = list(before)
    aioclient_mock.post(GRAPH_API_URL, side_effect=lambda *_: answer(shipments))
    await set_up(hass, freezer)

    seen: list[str] = []
    hass.bus.async_listen(
        "state_changed",
        lambda call: (
            seen.append(call.data["new_state"].attributes.get("event_type"))
            if call.data["entity_id"] == EVENT_ENTITY and call.data["new_state"]
            else None
        ),
    )

    shipments[:] = after
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert seen == ["ready_for_pickup", "delivered"], "one event for each package that changed"
