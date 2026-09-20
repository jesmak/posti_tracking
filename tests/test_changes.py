"""What happened to the packages between two updates."""

from __future__ import annotations

from typing import Any

from custom_components.posti_tracking.changes import (
    EVENT_DELIVERED,
    EVENT_MOVED,
    EVENT_NEW,
    EVENT_READY_FOR_PICKUP,
    EVENT_RETURNED,
    detect_changes,
)


def package(number: str, status: int, changed: str = "2026-09-16T05:30:00Z", **rest: Any) -> dict[str, Any]:
    return {
        "shipment_number": number,
        "status": status,
        "raw_status": "IN_TRANSPORT",
        "latest_event": "Kuljetuksessa",
        "latest_event_city": "HELSINKI",
        "latest_event_date": changed,
        "origin": "Verkkokauppa.com Oyj",
        "destination": "K-Market Keskusta",
        "destination_city": "LAPPEENRANTA",
        "source": "Posti",
        **rest,
    }


def types(changes: list) -> list[str]:
    return [change.event_type for change in changes]


def test_the_first_update_is_not_news() -> None:
    assert detect_changes(None, [package("JJFI0001", 3)]) == []


def test_a_package_that_was_not_there_before() -> None:
    assert types(detect_changes([], [package("JJFI0001", 1)])) == [EVENT_NEW]


def test_a_status_of_its_own_gets_an_event_of_its_own() -> None:
    before = [package("JJFI0001", 3), package("JJFI0002", 4), package("JJFI0003", 3)]
    after = [package("JJFI0001", 5), package("JJFI0002", 0), package("JJFI0003", 6)]
    assert types(detect_changes(before, after)) == [EVENT_READY_FOR_PICKUP, EVENT_DELIVERED, EVENT_RETURNED]


def test_any_other_step_is_a_package_moving() -> None:
    before = [package("JJFI0001", 2)]
    assert types(detect_changes(before, [package("JJFI0001", 3)])) == [EVENT_MOVED]


def test_a_new_event_without_a_new_status_is_a_package_moving() -> None:
    before = [package("JJFI0001", 3, "2026-09-16T05:30:00Z")]
    after = [package("JJFI0001", 3, "2026-09-16T07:15:00Z")]
    assert types(detect_changes(before, after)) == [EVENT_MOVED]


def test_a_package_that_did_not_change_is_not_news() -> None:
    before = [package("JJFI0001", 3)]
    assert detect_changes(before, [package("JJFI0001", 3)]) == []


def test_several_changes_in_one_update_are_several_events() -> None:
    before = [package("JJFI0001", 3), package("JJFI0002", 4)]
    after = [package("JJFI0001", 5), package("JJFI0002", 0), package("JJFI0003", 1)]
    assert types(detect_changes(before, after)) == [EVENT_READY_FOR_PICKUP, EVENT_DELIVERED, EVENT_NEW]


def test_an_event_carries_the_package_it_happened_to() -> None:
    [change] = detect_changes([], [package("JJFI0001", 5)])
    assert change.attributes == {
        "shipment_number": "JJFI0001",
        "status": 5,
        "raw_status": "IN_TRANSPORT",
        "origin": "Verkkokauppa.com Oyj",
        "destination": "K-Market Keskusta",
        "destination_city": "LAPPEENRANTA",
        "latest_event": "Kuljetuksessa",
        "latest_event_city": "HELSINKI",
        "latest_event_date": "2026-09-16T05:30:00Z",
        "source": "Posti",
    }
