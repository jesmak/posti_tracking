"""Turning OmaPosti's shipments into the sensor's packages."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from custom_components.posti_tracking.shipments import Packages, PackageSettings, build_packages, map_raw_status

from .conftest import NOW, SHIPMENTS, event, shipment

SETTINGS = PackageSettings(
    prioritize_undelivered=True,
    max_shipments=5,
    stale_shipment_day_limit=15,
    completed_shipment_days_shown=3,
    pickup_code="none",
)


def numbers(packages: Packages) -> list[str]:
    return [package["shipment_number"] for package in packages.packages]


@pytest.mark.parametrize(
    ("phase", "status"),
    [
        ("DELIVERED", 0),
        ("WAITING", 1),
        ("RECEIVED", 2),
        ("IN_TRANSPORT", 3),
        ("IN_DELIVERY", 4),
        ("READY_FOR_PICKUP", 5),
        ("RETURNED_TO_SENDER", 6),
        ("SOMETHING_NEW", 7),
        (None, 7),
    ],
)
def test_posti_phases(phase: str | None, status: int) -> None:
    assert map_raw_status(phase) == status


def test_unfinished_packages_come_first_and_old_ones_are_hidden() -> None:
    packages = build_packages(SHIPMENTS, SETTINGS, "fi", NOW)
    assert numbers(packages) == ["JJFI0002", "JJFI0008", "JJFI0001", "JJFI0004", "JJFI0003"]
    assert packages.latest_change.isoformat() == "2026-09-16T05:30:00+00:00"


def test_newest_first_when_undelivered_packages_are_not_prioritised() -> None:
    settings = replace(SETTINGS, prioritize_undelivered=False, max_shipments=3)
    assert numbers(build_packages(SHIPMENTS, settings, "fi", NOW)) == ["JJFI0002", "JJFI0008", "JJFI0004"]


def test_package_attributes() -> None:
    ready = build_packages(SHIPMENTS, SETTINGS, "fi", NOW).packages[2]
    assert ready == {
        "origin": "Example Shop Oy",
        "origin_city": "HELSINKI",
        "destination": "Example Market Keskusta, Pakettiautomaatti",
        "destination_city": "LAPPEENRANTA",
        "shipment_number": "JJFI0001",
        "shipment_date": "2026-09-14T06:00:00Z",
        "status": 5,
        "raw_status": "READY_FOR_PICKUP",
        "latest_event": "Noudettavissa",
        "latest_event_city": "LAPPEENRANTA",
        "latest_event_country": "FI",
        "latest_event_date": "2026-09-15T07:00:00Z",
        "estimated_delivery": None,
        "pickup_deadline": None,
        "weight": None,
        "package_count": None,
        "pickup_point": None,
        "pickup_code": None,
        "pickup_qr": None,
        "source": "Posti",
        "tracking_url": "https://www.posti.fi/fi/seuranta#/lahetys/JJFI0001",
    }
    assert build_packages(SHIPMENTS, SETTINGS, "en", NOW).packages[2]["latest_event"] == "Ready for pickup"


def test_missing_details_have_fallbacks() -> None:
    home_delivery = shipment(
        "JJFI0009",
        "IN_DELIVERY",
        {"eventDescription": [{"lang": "en", "value": "In delivery"}], "timestamp": "2026-09-16T06:00:00Z"},
        trackingNumbers=[],
        parties=[{"name": ["Example Shop Oy"], "role": "CONSIGNOR"}, {"name": ["Matti"], "role": "CONSIGNEE"}],
    )
    [package] = build_packages([home_delivery], SETTINGS, "fi", NOW).packages
    assert package["shipment_number"] == "S-JJFI0009", "the shipment number without tracking numbers"
    assert package["destination"] == "Matti", "the receiver without a pickup point"
    assert package["latest_event"] == "In delivery", "another language without a Finnish description"
    assert package["latest_event_city"] is None


def test_times_are_utc() -> None:
    [package] = build_packages([shipment("JJFI0010", "RECEIVED", event("2026-09-16T08:59:00", "x", "x"))], SETTINGS, "fi", NOW).packages
    assert package["status"] == 2
    assert build_packages([], SETTINGS, "fi", NOW) == Packages(None, [], [], [])


def with_details(**changes: Any) -> dict[str, Any]:
    """A shipment with the fields Posti fills in besides the events."""
    return shipment(
        "JJFI0020",
        "IN_TRANSPORT",
        event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport"),
        estimatedDeliveryTime="2026-09-17T10:00:00Z",
        grossWeight=1.25,
        packageQuantity=2,
        pickupPoint={
            "type": "LOCKER",
            "lockerAddress": "Example Market Keskusta",
            "lockerCode": "123456",
            "lockerID": "17",
            "showLockerID": True,
            "pupCode": "PUP-1",
            "availabilityTime": "24h",
            "location": {"street1": "Esimerkkikatu 1", "postCode": "53100", "city": "LAPPEENRANTA"},
        },
        **changes,
    )


def test_a_package_carries_what_posti_knows_of_it() -> None:
    [package] = build_packages([with_details()], SETTINGS, "fi", NOW).packages
    assert package["estimated_delivery"] == "2026-09-17T10:00:00Z"
    assert package["weight"] == 1.25
    assert package["package_count"] == 2
    assert package["pickup_deadline"] is None, "Posti doesn't say how long a package is kept"


POINT = {
    "name": "Example Market Keskusta",
    "compartment": "17",
    "street": "Esimerkkikatu 1",
    "postal_code": "53100",
    "city": "LAPPEENRANTA",
    "type": "LOCKER",
    "available": "24h",
}


def test_the_pickup_point_is_always_there_but_the_code_and_the_door_only_when_asked_for() -> None:
    [package] = build_packages([with_details()], SETTINGS, "fi", NOW).packages
    assert package["pickup_point"] == {**POINT, "compartment": None}
    assert package["pickup_code"] is None
    assert package["pickup_qr"] is None


def test_the_code_and_the_door_when_asked_for() -> None:
    settings = replace(SETTINGS, pickup_code="code")
    [package] = build_packages([with_details()], settings, "fi", NOW).packages
    assert package["pickup_point"] == POINT
    assert package["pickup_code"] == "123456"
    assert package["pickup_qr"] is None


@pytest.mark.parametrize("point_type", ["POSTI", "NOUTOPISTE"])
def test_a_counter_gets_a_qr_code_of_the_pickup_code(point_type: str) -> None:
    settings = replace(SETTINGS, pickup_code="code_and_qr")
    counter = {"type": point_type, "lockerID": "B0001", "pupCode": "PUP-1", "location": {"city": "LAPPEENRANTA"}}
    [package] = build_packages([{**with_details(), "pickupPoint": counter}], settings, "fi", NOW).packages
    assert package["pickup_code"] == "PUP-1"
    assert package["pickup_qr"] == "PUP-1"


@pytest.mark.parametrize("point_type", ["LOCKER", "SMARTPOST", "MUSE_LOCKER", "UNKNOWN", None])
def test_a_locker_or_an_unknown_point_gets_no_qr_code(point_type: str | None) -> None:
    settings = replace(SETTINGS, pickup_code="code_and_qr")
    point = {**with_details()["pickupPoint"], "type": point_type}
    [package] = build_packages([{**with_details(), "pickupPoint": point}], settings, "fi", NOW).packages
    assert package["pickup_code"] == "123456"
    assert package["pickup_qr"] is None


def test_a_compartment_posti_does_not_show_is_left_out() -> None:
    settings = replace(SETTINGS, pickup_code="code")
    counter = {"lockerID": "B0001", "showLockerID": None, "pupCode": "PUP-1", "location": {"city": "LAPPEENRANTA"}}
    [package] = build_packages([{**with_details(), "pickupPoint": counter}], settings, "fi", NOW).packages
    assert package["pickup_point"]["compartment"] is None
    assert package["pickup_code"] == "PUP-1"


def test_a_package_without_the_extra_fields() -> None:
    settings = replace(SETTINGS, pickup_code="code_and_qr")
    plain = shipment("JJFI0021", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport"))
    [package] = build_packages([plain], settings, "fi", NOW).packages
    assert package["estimated_delivery"] is None
    assert package["weight"] is None
    assert package["package_count"] is None
    assert package["pickup_point"] is None
    assert package["pickup_code"] is None
    assert package["pickup_qr"] is None


def test_the_counts_and_events_see_every_package_not_only_the_listed_ones() -> None:
    moving = [
        shipment(f"JJFI03{n}", "IN_TRANSPORT", event(f"2026-09-16T0{n}:00:00Z", "Kuljetuksessa", "In transport")) for n in range(1, 4)
    ]
    stale = shipment("JJFI0399", "IN_TRANSPORT", event("2026-08-01T05:00:00Z", "Kuljetuksessa", "In transport"))
    packages = build_packages([*moving, stale], replace(SETTINGS, max_shipments=2), "fi", NOW)
    assert numbers(packages) == ["JJFI033", "JJFI032"]
    assert len(packages.current) == 3, "a count covers all of them, but not one gone stale"
    assert len(packages.every) == 4, "events follow every package, stale ones too"


def test_the_newest_event_is_found_by_its_time_not_its_place() -> None:
    newest_first = shipment(
        "JJFI0400",
        "READY_FOR_PICKUP",
        event("2026-09-16T09:30:00Z", "Noudettavissa", "Ready for pickup"),
        event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport"),
    )
    [package] = build_packages([newest_first], SETTINGS, "fi", NOW).packages
    assert package["latest_event"] == "Noudettavissa"
    assert package["latest_event_date"] == "2026-09-16T09:30:00Z"


@pytest.mark.parametrize("setting", ["None", "codes", ""])
def test_a_setting_that_is_not_known_keeps_the_code_out(setting: str) -> None:
    [package] = build_packages([with_details()], replace(SETTINGS, pickup_code=setting), "fi", NOW).packages
    assert package["pickup_code"] is None
    assert package["pickup_point"]["compartment"] is None


def test_the_qr_code_holds_the_counters_code_even_when_a_locker_code_is_sent_too() -> None:
    counter = {"type": "POSTI", "lockerCode": "LOCKER-1", "pupCode": "PUP-1", "location": {"city": "LAPPEENRANTA"}}
    [package] = build_packages(
        [{**with_details(), "pickupPoint": counter}], replace(SETTINGS, pickup_code="code_and_qr"), "fi", NOW
    ).packages
    assert package["pickup_qr"] == "PUP-1"


def test_a_package_listed_twice_is_kept_once_as_its_newest_copy() -> None:
    newer = shipment("JJFI0600", "READY_FOR_PICKUP", event("2026-09-16T09:30:00Z", "Noudettavissa", "Ready for pickup"))
    older = shipment("JJFI0600", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport"))
    for twice in ([newer, older], [older, newer]):
        packages = build_packages(twice, SETTINGS, "fi", NOW)
        assert [package["status"] for package in packages.every] == [5]


def test_a_package_without_a_number_is_left_out() -> None:
    nameless = {**shipment("x", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport")), "trackingNumbers": []}
    nameless.pop("shipmentNumber")
    assert build_packages([nameless], SETTINGS, "fi", NOW).every == []
