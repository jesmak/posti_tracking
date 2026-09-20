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
    include_pickup_details=False,
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
        "origin": "Verkkokauppa.com Oyj",
        "origin_city": "HELSINKI",
        "destination": "K-Market Keskusta, Pakettiautomaatti",
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
        "source": "Posti",
    }
    assert build_packages(SHIPMENTS, SETTINGS, "en", NOW).packages[2]["latest_event"] == "Ready for pickup"


def test_missing_details_have_fallbacks() -> None:
    home_delivery = shipment(
        "JJFI0009",
        "IN_DELIVERY",
        {"eventDescription": [{"lang": "en", "value": "In delivery"}], "timestamp": "2026-09-16T06:00:00Z"},
        trackingNumbers=[],
        parties=[{"name": ["Verkkokauppa.com Oyj"], "role": "CONSIGNOR"}, {"name": ["Matti"], "role": "CONSIGNEE"}],
    )
    [package] = build_packages([home_delivery], SETTINGS, "fi", NOW).packages
    assert package["shipment_number"] == "S-JJFI0009", "the shipment number without tracking numbers"
    assert package["destination"] == "Matti", "the receiver without a pickup point"
    assert package["latest_event"] == "In delivery", "another language without a Finnish description"
    assert package["latest_event_city"] is None


def test_times_are_utc() -> None:
    [package] = build_packages(
        [shipment("JJFI0010", "RECEIVED", event("2026-09-16T08:59:00", "x", "x"))], SETTINGS, "fi", NOW
    ).packages
    assert package["status"] == 2
    assert build_packages([], SETTINGS, "fi", NOW) == Packages(None, [])


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
            "lockerAddress": "K-Market Keskusta",
            "lockerCode": "123456",
            "pupCode": "PUP-1",
            "availabilityTime": "24h",
            "location": {"street1": "Kauppakatu 1", "postCode": "53100", "city": "LAPPEENRANTA"},
        },
        **changes,
    )


def test_a_package_carries_what_posti_knows_of_it() -> None:
    [package] = build_packages([with_details()], SETTINGS, "fi", NOW).packages
    assert package["estimated_delivery"] == "2026-09-17T10:00:00Z"
    assert package["weight"] == 1.25
    assert package["package_count"] == 2
    assert package["pickup_deadline"] is None, "Posti doesn't say how long a package is kept"


def test_the_pickup_point_and_its_code_are_left_out_unless_asked_for() -> None:
    [package] = build_packages([with_details()], SETTINGS, "fi", NOW).packages
    assert package["pickup_point"] is None
    assert package["pickup_code"] is None


def test_the_pickup_point_and_its_code_when_asked_for() -> None:
    settings = replace(SETTINGS, include_pickup_details=True)
    [package] = build_packages([with_details()], settings, "fi", NOW).packages
    assert package["pickup_point"] == {
        "name": "K-Market Keskusta",
        "street": "Kauppakatu 1",
        "postal_code": "53100",
        "city": "LAPPEENRANTA",
        "type": "LOCKER",
        "available": "24h",
    }
    assert package["pickup_code"] == "123456"


def test_a_package_without_the_extra_fields() -> None:
    settings = replace(SETTINGS, include_pickup_details=True)
    plain = shipment("JJFI0021", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport"))
    [package] = build_packages([plain], settings, "fi", NOW).packages
    assert package["estimated_delivery"] is None
    assert package["weight"] is None
    assert package["package_count"] is None
    assert package["pickup_point"] is None
    assert package["pickup_code"] is None
