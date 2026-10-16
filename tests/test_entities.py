"""The counts of an account's packages, and the events its packages fire."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse

from custom_components.posti_tracking.const import DOMAIN, GRAPH_API_URL

from .conftest import ENTRY_DATA, NOW, USERNAME, event, page_of, shipment

EVENT_ENTITY = "event.posti_matti_meikalainen_example_com_package"
ON_THE_WAY = "sensor.posti_matti_meikalainen_example_com_incoming_on_the_way"
READY = "sensor.posti_matti_meikalainen_example_com_incoming_ready_for_pickup"
SENT_ON_THE_WAY = "sensor.posti_matti_meikalainen_example_com_sent_on_the_way"
SENT_READY = "sensor.posti_matti_meikalainen_example_com_sent_ready_for_pickup"


async def answer(shipments: list[dict[str, Any]]) -> AiohttpClientMockResponse:
    """OmaPosti's answer, read afresh each time so a test can change the shipments."""
    return AiohttpClientMockResponse("post", GRAPH_API_URL, json=page_of(list(shipments)))


def account(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=USERNAME, version=1, minor_version=5, unique_id=USERNAME, data=ENTRY_DATA)
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
    hass: HomeAssistant, omaposti: AiohttpClientMocker, renewal: Any, freezer: FrozenDateTimeFactory
) -> None:
    await set_up(hass, freezer)

    # Of the packages shown: three are on their way, one of them ready for pickup.
    assert hass.states.get(ON_THE_WAY).state == "3"
    assert hass.states.get(ON_THE_WAY).attributes["state_class"] == "measurement"
    assert hass.states.get(READY).state == "1"
    registry = er.async_get(hass)
    assert registry.async_get(ON_THE_WAY).unique_id == f"posti_{USERNAME}_on_the_way"
    assert registry.async_get(READY).unique_id == f"posti_{USERNAME}_ready_for_pickup"


async def test_the_packages_already_there_are_not_events(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, renewal: Any, freezer: FrozenDateTimeFactory
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
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any, freezer: FrozenDateTimeFactory
) -> None:
    moving = shipment("JJFI0100", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa"))
    arrived = shipment(
        "JJFI0100",
        "READY_FOR_PICKUP",
        event("2026-09-16T05:30:00Z", "Kuljetuksessa"),
        event("2026-09-16T09:30:00Z", "Noudettavissa"),
        pickupPoint={"type": "LOCKER", "address": {"publicName": "Example Market Keskusta", "city": "LAPPEENRANTA"}},
    )
    shipments = [moving]
    aioclient_mock.post(GRAPH_API_URL, side_effect=lambda *_: answer(shipments))

    await set_up(hass, freezer)
    assert hass.states.get(EVENT_ENTITY).state == "unknown"

    fired: list[State] = []
    hass.bus.async_listen(
        "state_changed",
        callback(lambda call: fired.append(call.data["new_state"]) if call.data["entity_id"] == EVENT_ENTITY else None),
    )

    shipments[:] = [arrived]
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(EVENT_ENTITY).attributes["event_type"] == "ready_for_pickup"
    assert hass.states.get(EVENT_ENTITY).attributes["shipment_number"] == "JJFI0100"
    assert hass.states.get(EVENT_ENTITY).attributes["destination"] == "Example Market Keskusta"
    assert hass.states.get(EVENT_ENTITY).attributes["direction"] == "incoming"
    assert hass.states.get(READY).state == "1"
    assert [state.attributes.get("event_type") for state in fired if state] == ["ready_for_pickup"]


async def test_every_change_of_one_update_fires_its_own_event(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any, freezer: FrozenDateTimeFactory
) -> None:
    before = [
        shipment("JJFI0201", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa")),
        shipment("JJFI0202", "IN_DELIVERY", event("2026-09-16T05:40:00Z", "Jakelussa")),
    ]
    after = [
        shipment(
            "JJFI0201",
            "READY_FOR_PICKUP",
            event("2026-09-16T05:30:00Z", "Kuljetuksessa"),
            event("2026-09-16T09:30:00Z", "Noudettavissa"),
        ),
        shipment(
            "JJFI0202",
            "DELIVERED",
            event("2026-09-16T05:40:00Z", "Jakelussa"),
            event("2026-09-16T09:31:00Z", "Toimitettu"),
        ),
    ]
    shipments = list(before)
    aioclient_mock.post(GRAPH_API_URL, side_effect=lambda *_: answer(shipments))
    await set_up(hass, freezer)

    seen: list[str] = []
    hass.bus.async_listen(
        "state_changed",
        callback(
            lambda call: (
                seen.append(call.data["new_state"].attributes.get("event_type"))
                if call.data["entity_id"] == EVENT_ENTITY and call.data["new_state"]
                else None
            )
        ),
    )

    shipments[:] = after
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert seen == ["ready_for_pickup", "delivered"], "one event for each package that changed"


async def failure(method: str, url: str) -> AiohttpClientMockResponse:
    return AiohttpClientMockResponse(method, url, status=503)


async def test_an_event_after_a_failed_update_does_not_come_from_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any, freezer: FrozenDateTimeFactory
) -> None:
    current = [shipment("JJFI0500", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa"))]
    down = False
    aioclient_mock.post(GRAPH_API_URL, side_effect=lambda *_: failure("post", GRAPH_API_URL) if down else answer(current))
    await set_up(hass, freezer)

    down = True
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(EVENT_ENTITY).state == "unavailable"

    writes: list[tuple[str, str | None]] = []
    hass.bus.async_listen(
        "state_changed",
        callback(
            lambda call: (
                writes.append((call.data["old_state"].state, call.data["new_state"].attributes.get("event_type")))
                if call.data["entity_id"] == EVENT_ENTITY
                else None
            )
        ),
    )
    down = False
    current[:] = [
        shipment(
            "JJFI0500",
            "READY_FOR_PICKUP",
            event("2026-09-16T05:30:00Z", "Kuljetuksessa"),
            event("2026-09-16T09:30:00Z", "Noudettavissa"),
        )
    ]
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # An automation leaves out a change from unavailable, as the entity coming back, so the event must not be one.
    [(before, _)] = [write for write in writes if write[1] == "ready_for_pickup"]
    assert before != "unavailable"


async def test_sent_packages_are_counted_apart(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any, freezer: FrozenDateTimeFactory
) -> None:
    shipments = [
        shipment("JJFI0601", "READY_FOR_PICKUP", event("2026-09-16T08:30:00Z", "Noudettavissa")),
        shipment("JJFI0602", "READY_FOR_PICKUP", event("2026-09-16T08:40:00Z", "Noudettavissa"), role="SENDER"),
        shipment("JJFI0603", "IN_TRANSPORT", event("2026-09-16T08:50:00Z", "Kuljetuksessa"), role="SENDER"),
    ]
    aioclient_mock.post(GRAPH_API_URL, side_effect=lambda *_: answer(shipments))
    await set_up(hass, freezer)

    assert hass.states.get(ON_THE_WAY).state == "1"
    assert hass.states.get(READY).state == "1"
    assert hass.states.get(SENT_ON_THE_WAY).state == "2"
    assert hass.states.get(SENT_READY).state == "1"
    assert er.async_get(hass).async_get(SENT_ON_THE_WAY).unique_id == f"posti_{USERNAME}_outgoing_on_the_way"
