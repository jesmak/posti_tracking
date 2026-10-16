"""Turning OmaPosti's shipments into the sensor's packages."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from custom_components.posti_tracking.shipments import Packages, PackageSettings, build_packages, map_status, opening_hours

from .conftest import NOW, SHIPMENTS, event, shipment

SETTINGS = PackageSettings(
    prioritize_undelivered=True,
    max_shipments=5,
    max_outgoing_shipments=5,
    stale_shipment_day_limit=15,
    completed_shipment_days_shown=3,
    pickup_code="none",
)


def numbers(packages: Packages) -> list[str]:
    return [package["shipment_number"] for package in packages.packages]


@pytest.mark.parametrize(
    ("main", "status"),
    [
        ("DELIVERED", 0),
        ("WAITING", 1),
        ("ORDER_RECEIVED", 1),
        ("RECEIVED", 2),
        ("IN_TRANSPORT", 3),
        ("IN_DELIVERY", 4),
        ("READY_FOR_PICKUP", 5),
        ("SOMETHING_NEW", 7),
        (None, 7),
    ],
)
def test_posti_statuses(main: str | None, status: int) -> None:
    assert map_status(main, "incoming") == status
    assert map_status(main, "outgoing") == status


@pytest.mark.parametrize("main", ["RETURN_WAITING", "RETURN_IN_TRANSPORT", "RETURN_READY_FOR_PICKUP", "RETURN_DELIVERED"])
def test_a_received_shipment_going_back_is_returned_to_its_sender(main: str) -> None:
    assert map_status(main, "incoming") == 6
    assert map_status(main, None) == 6


@pytest.mark.parametrize(
    ("main", "status"), [("RETURN_WAITING", 1), ("RETURN_IN_TRANSPORT", 3), ("RETURN_READY_FOR_PICKUP", 5), ("RETURN_DELIVERED", 6)]
)
def test_a_sent_shipment_coming_back_goes_through_the_statuses_again(main: str, status: int) -> None:
    # It comes back to the user, who picks it up.
    assert map_status(main, "outgoing") == status


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
        "origin_city": None,
        "recipient": None,
        "destination": None,
        "destination_city": "LAPPEENRANTA",
        "shipment_number": "JJFI0001",
        "shipment_date": None,
        "status": 5,
        "raw_status": "READY_FOR_PICKUP",
        "direction": "incoming",
        "latest_event": "Noudettavissa",
        "latest_event_city": "LAPPEENRANTA",
        "latest_event_country": None,
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


def test_missing_details_have_fallbacks() -> None:
    home_delivery = shipment(
        "JJFI0009",
        "IN_DELIVERY",
        {"eventDescription": "Jakelussa", "timestamp": "2026-09-16T06:00:00Z"},
        displayId=None,
        delivery={"destination": {"name": "Matti Meikäläinen", "city": "LAPPEENRANTA"}},
    )
    [package] = build_packages([home_delivery], SETTINGS, "fi", NOW).packages
    assert package["shipment_number"] == "S-JJFI0009", "the shipment id without a tracking number"
    assert package["destination"] == "Matti Meikäläinen", "where it is delivered without a pickup point"
    assert package["latest_event_city"] is None


def test_times_are_utc() -> None:
    [package] = build_packages([shipment("JJFI0010", "RECEIVED", event("2026-09-16T08:59:00", "x"))], SETTINGS, "fi", NOW).packages
    assert package["status"] == 2
    assert build_packages([], SETTINGS, "fi", NOW) == Packages(None, [], [], {})


HOURS = [
    *(
        {"dayOfWeek": day, "opens": "07:00:00", "closes": "21:00:00", "open24h": False, "closed": False}
        for day in ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY")
    ),
    {"dayOfWeek": "SATURDAY", "opens": "10:00:00", "closes": "18:00:00", "open24h": False, "closed": False},
    {"dayOfWeek": "SUNDAY", "opens": None, "closes": None, "open24h": False, "closed": True},
]


def with_details(method: str = "LOCKER_CODE", **changes: Any) -> dict[str, Any]:
    """A shipment ready for pickup, with the fields Posti fills in besides the events. Made-up codes and places."""
    return shipment(
        "JJFI0020",
        "READY_FOR_PICKUP",
        event("2026-09-16T05:30:00Z", "Noudettavissa"),
        delivery={"time": {"timestamp": "2026-09-17T10:00:00Z"}, "destination": {"name": None, "city": "LAPPEENRANTA"}},
        measurements={"weight": {"value": "1.25"}, "packageQuantity": {"value": "2"}},
        pickupPoint={
            "type": "LOCKER",
            "lastCollectionDate": "2026-09-23",
            "pickupDetails": {"pickupMethod": method, "pinCode": "123456", "lockerId": "17/3", "shelfId": "B12", "itemId": "ITEM-1"},
            "address": {
                "streetAddress": "Esimerkkikatu 1",
                "postcode": "53100",
                "city": "LAPPEENRANTA",
                "publicName": "Example Market Keskusta",
            },
            "openingHours": HOURS,
        },
        **changes,
    )


def test_a_package_carries_what_posti_knows_of_it() -> None:
    [package] = build_packages([with_details()], SETTINGS, "fi", NOW).packages
    assert package["estimated_delivery"] == "2026-09-17T10:00:00Z"
    assert package["weight"] == 1.25
    assert package["package_count"] == 2
    assert package["pickup_deadline"] == "2026-09-23", "how long Posti keeps it"
    assert package["destination"] == "Example Market Keskusta", "the pickup point stands in for the destination"


POINT = {
    "name": "Example Market Keskusta",
    "compartment": "17",
    "street": "Esimerkkikatu 1",
    "postal_code": "53100",
    "city": "LAPPEENRANTA",
    "type": "LOCKER",
    "available": "ma-pe 7.00 - 21.00, la 10.00 - 18.00, su suljettu",
}


def test_the_pickup_point_is_always_there_but_the_code_and_the_door_only_when_asked_for() -> None:
    [package] = build_packages([with_details()], SETTINGS, "fi", NOW).packages
    assert package["pickup_point"] == {**POINT, "compartment": None}
    assert package["pickup_code"] is None
    assert package["pickup_qr"] is None


def test_a_locker_code_and_the_door_when_asked_for() -> None:
    # The door is lockerId before its slash. A locker takes the code on its keypad, so there is no QR code.
    [package] = build_packages([with_details()], replace(SETTINGS, pickup_code="code_and_qr"), "fi", NOW).packages
    assert package["pickup_point"] == POINT
    assert package["pickup_code"] == "123456"
    assert package["pickup_qr"] is None


def test_a_pickup_code_is_its_qr_code_too() -> None:
    [package] = build_packages([with_details("PICKUP_CODE")], replace(SETTINGS, pickup_code="code_and_qr"), "fi", NOW).packages
    assert package["pickup_code"] == "123456"
    assert package["pickup_qr"] == "123456"
    assert package["pickup_point"]["compartment"] is None, "a pickup code has no door"


def test_a_counter_shelf_gets_a_qr_code_of_the_item_and_no_code() -> None:
    [package] = build_packages([with_details("QR_CODE")], replace(SETTINGS, pickup_code="code_and_qr"), "fi", NOW).packages
    assert package["pickup_code"] is None
    assert package["pickup_qr"] == "ITEM-1"
    assert package["pickup_point"]["compartment"] == "B12", "the shelf it is kept on"


def test_the_qr_code_holds_what_the_web_app_puts_in_it_not_another_number_posti_sends() -> None:
    # v2 once got this wrong with two codes. AppSync's pickupMethod says which one the QR code holds, as OmaPosti's web
    # app reads it: with a pickup code the code, not the item's id beside it.
    [package] = build_packages([with_details("PICKUP_CODE")], replace(SETTINGS, pickup_code="code_and_qr"), "fi", NOW).packages
    assert package["pickup_qr"] == "123456"
    [package] = build_packages([with_details("QR_CODE")], replace(SETTINGS, pickup_code="code_and_qr"), "fi", NOW).packages
    assert package["pickup_qr"] == "ITEM-1"


@pytest.mark.parametrize("method", ["PICKUP_CODE", "QR_CODE"])
def test_the_code_alone_has_no_qr_code(method: str) -> None:
    [package] = build_packages([with_details(method)], replace(SETTINGS, pickup_code="code"), "fi", NOW).packages
    assert package["pickup_qr"] is None


def test_an_outdoor_locker_has_a_keypad_not_a_door() -> None:
    # The number before lockerId's slash is the keypad to type the code on, so it isn't shown as a door.
    outdoor = with_details()
    outdoor["pickupPoint"] = {**outdoor["pickupPoint"], "type": "LOCKER_OUTDOOR"}
    [package] = build_packages([outdoor], replace(SETTINGS, pickup_code="code"), "fi", NOW).packages
    assert package["pickup_code"] == "123456"
    assert package["pickup_point"]["compartment"] is None


@pytest.mark.parametrize("method", ["SOMETHING_NEW", None])
def test_a_pickup_method_not_known_gives_no_code(method: str | None) -> None:
    [package] = build_packages([with_details(method)], replace(SETTINGS, pickup_code="code_and_qr"), "fi", NOW).packages
    assert package["pickup_code"] is None
    assert package["pickup_qr"] is None
    assert package["pickup_point"]["compartment"] is None


def test_the_day_limits_count_the_hours_too() -> None:
    # Delivered two days and an hour ago is past a limit of two days.
    delivered = shipment("JJFI0030", "DELIVERED", event((NOW - timedelta(days=2, hours=1)).isoformat(), "Toimitettu"))
    assert build_packages([delivered], replace(SETTINGS, completed_shipment_days_shown=2), "fi", NOW).packages == []
    assert numbers(build_packages([delivered], replace(SETTINGS, completed_shipment_days_shown=3), "fi", NOW)) == ["JJFI0030"]


def test_a_package_is_known_by_posti_s_shipment_id_when_its_tracking_number_changes() -> None:
    before = shipment("JJFI0031", "RECEIVED", event("2026-09-16T05:30:00Z", "x"), shipmentId="S-31", displayId=None)
    after = {**before, "displayId": "JJFI0031X"}
    assert list(build_packages([before], SETTINGS, "fi", NOW).every) == ["S-31"]
    assert list(build_packages([after], SETTINGS, "fi", NOW).every) == ["S-31"]


def test_odd_values_are_left_out() -> None:
    odd = with_details(displayName=["Example Shop"], events=[None, "x", event("2026-09-16T05:30:00Z", "Noudettavissa")])
    odd["pickupPoint"] = {**odd["pickupPoint"], "address": "Esimerkkikatu 1", "openingHours": [None, {"dayOfWeek": "MONDAY"}]}
    [package] = build_packages([odd], SETTINGS, "fi", NOW).packages
    assert package["origin"] is None
    assert package["latest_event"] == "Noudettavissa"
    assert package["pickup_point"] == {
        "name": None,
        "compartment": None,
        "street": None,
        "postal_code": None,
        "city": None,
        "type": "LOCKER",
        "available": None,
    }


def test_a_package_without_the_extra_fields() -> None:
    settings = replace(SETTINGS, pickup_code="code_and_qr")
    plain = shipment("JJFI0021", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa"))
    [package] = build_packages([plain], settings, "fi", NOW).packages
    assert package["estimated_delivery"] is None
    assert package["weight"] is None
    assert package["package_count"] is None
    assert package["pickup_point"] is None
    assert package["pickup_code"] is None
    assert package["pickup_qr"] is None


def test_the_counts_and_events_see_every_package_not_only_the_listed_ones() -> None:
    moving = [shipment(f"JJFI03{n}", "IN_TRANSPORT", event(f"2026-09-16T0{n}:00:00Z", "Kuljetuksessa")) for n in range(1, 4)]
    stale = shipment("JJFI0399", "IN_TRANSPORT", event("2026-08-01T05:00:00Z", "Kuljetuksessa"))
    packages = build_packages([*moving, stale], replace(SETTINGS, max_shipments=2), "fi", NOW)
    assert numbers(packages) == ["JJFI033", "JJFI032"]
    assert len(packages.current) == 3, "a count covers all of them, but not one gone stale"
    assert len(packages.every) == 4, "events follow every package, stale ones too"


def test_the_newest_event_is_found_by_its_time_not_its_place() -> None:
    newest_first = shipment(
        "JJFI0400",
        "READY_FOR_PICKUP",
        event("2026-09-16T09:30:00Z", "Noudettavissa"),
        event("2026-09-16T05:30:00Z", "Kuljetuksessa"),
    )
    [package] = build_packages([newest_first], SETTINGS, "fi", NOW).packages
    assert package["latest_event"] == "Noudettavissa"
    assert package["latest_event_date"] == "2026-09-16T09:30:00Z"


@pytest.mark.parametrize("setting", ["None", "codes", ""])
def test_a_setting_that_is_not_known_keeps_the_code_out(setting: str) -> None:
    [package] = build_packages([with_details()], replace(SETTINGS, pickup_code=setting), "fi", NOW).packages
    assert package["pickup_code"] is None
    assert package["pickup_point"]["compartment"] is None


def test_a_package_listed_twice_is_kept_once_as_its_newest_copy() -> None:
    newer = shipment("JJFI0600", "READY_FOR_PICKUP", event("2026-09-16T09:30:00Z", "Noudettavissa"))
    older = shipment("JJFI0600", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa"))
    for twice in ([newer, older], [older, newer]):
        packages = build_packages(twice, SETTINGS, "fi", NOW)
        assert [package["status"] for package in packages.every.values()] == [5]


def test_a_package_without_a_number_is_left_out() -> None:
    nameless = {**shipment("x", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa")), "displayId": None, "shipmentId": None}
    assert build_packages([nameless], SETTINGS, "fi", NOW).every == {}


@pytest.mark.parametrize(
    ("language", "line"),
    [("fi", "ma-pe 7.00 - 21.00, la 10.00 - 18.00, su suljettu"), ("en", "Mon-Fri 7:00 - 21:00, Sat 10:00 - 18:00, Sun closed")],
)
def test_opening_hours_as_one_line(language: str, line: str) -> None:
    assert opening_hours(HOURS, language) == line


def test_opening_hours_of_a_locker_open_all_day_and_of_missing_days() -> None:
    all_day = [
        {"dayOfWeek": day, "open24h": True} for day in ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
    ]
    assert opening_hours(all_day, "fi") == "ma-su 24 h"
    # Days Posti leaves out break a run of days, rather than being joined over.
    weekdays = [
        {"dayOfWeek": "MONDAY", "opens": "08:00:00", "closes": "16:00:00"},
        {"dayOfWeek": "WEDNESDAY", "opens": "08:00:00", "closes": "16:00:00"},
    ]
    assert opening_hours(weekdays, "en") == "Mon 8:00 - 16:00, Wed 8:00 - 16:00"
    assert opening_hours(None, "fi") is None
    assert opening_hours([], "fi") is None


def sent(number: str, status: str, *events: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A shipment the account sent: displayName is its recipient."""
    return shipment(number, status, *events, role="SENDER", displayName="Maija Mallikas", **changes)


def test_a_sent_package_says_so_and_names_its_recipient() -> None:
    [package] = build_packages(
        [sent("JJFI0700", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa"))], SETTINGS, "fi", NOW
    ).packages
    assert package["direction"] == "outgoing"
    assert package["origin"] is None, "its sender is the user"
    assert package["recipient"] == "Maija Mallikas", "who it goes to, in its own key"
    assert package["destination"] is None, "a place, not the recipient"


def test_a_package_whose_direction_is_not_known_goes_with_the_received_ones() -> None:
    unknown = shipment("JJFI0701", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa"), role="SOMEONE_ELSE")
    [package] = build_packages([unknown], replace(SETTINGS, max_outgoing_shipments=0), "fi", NOW).packages
    assert package["direction"] is None
    assert build_packages([unknown], replace(SETTINGS, max_shipments=0), "fi", NOW).packages == []


def test_each_direction_is_listed_up_to_its_own_maximum() -> None:
    received = [shipment(f"JJFI100{n}", "IN_TRANSPORT", event(f"2026-09-16T0{n}:00:00Z", "x")) for n in range(1, 4)]
    outgoing = [sent(f"JJFI200{n}", "IN_TRANSPORT", event(f"2026-09-16T0{n}:30:00Z", "x")) for n in range(1, 4)]
    packages = build_packages([*received, *outgoing], replace(SETTINGS, max_shipments=2, max_outgoing_shipments=1), "fi", NOW)
    assert numbers(packages) == ["JJFI2003", "JJFI1003", "JJFI1002"], "newest first, two received and one sent"
    assert len(packages.current) == 6, "the counts see all of them"


def test_sent_packages_do_not_push_received_ones_off_the_list() -> None:
    outgoing = [sent(f"JJFI300{n}", "IN_TRANSPORT", event(f"2026-09-16T0{n}:00:00Z", "x")) for n in range(2, 8)]
    waited_for = shipment("JJFI3100", "READY_FOR_PICKUP", event("2026-09-15T10:00:00Z", "x"))
    packages = build_packages([*outgoing, waited_for], SETTINGS, "fi", NOW)
    assert "JJFI3100" in numbers(packages)
    assert len(packages.packages) == 6, "five sent and the received one"


def test_each_direction_takes_its_undelivered_packages_first() -> None:
    waiting = shipment("JJFI6001", "IN_TRANSPORT", event("2026-09-15T05:00:00Z", "x"))
    delivered = shipment("JJFI6002", "DELIVERED", event("2026-09-16T08:00:00Z", "x"))
    outgoing = sent("JJFI6003", "DELIVERED", event("2026-09-16T07:00:00Z", "x"))
    packages = build_packages([waiting, delivered, outgoing], replace(SETTINGS, max_shipments=1, max_outgoing_shipments=1), "fi", NOW)
    assert numbers(packages) == ["JJFI6001", "JJFI6003"], "the undelivered received one, though older, and the sent one"


def test_a_maximum_of_0_leaves_that_direction_out_altogether() -> None:
    received = shipment("JJFI4001", "IN_TRANSPORT", event("2026-09-16T05:00:00Z", "x"))
    outgoing = sent("JJFI4002", "IN_TRANSPORT", event("2026-09-16T08:00:00Z", "x"))
    packages = build_packages([received, outgoing], replace(SETTINGS, max_outgoing_shipments=0), "fi", NOW)
    assert numbers(packages) == ["JJFI4001"]
    assert [package["shipment_number"] for package in packages.current] == ["JJFI4001"]
    assert list(packages.every) == ["S-JJFI4001"], "no events for it either"
    assert packages.latest_change.isoformat() == "2026-09-16T05:00:00+00:00", "nor does it change the sensor's time"


@pytest.mark.parametrize(("main", "status"), [("RETURN_IN_TRANSPORT", 3), ("RETURN_READY_FOR_PICKUP", 5), ("RETURN_DELIVERED", 6)])
def test_a_sent_package_coming_back_is_incoming(main: str, status: int) -> None:
    # It is on its way to the user, to be collected like any other: counted, notified of and listed as incoming.
    returned = sent("JJFI0800", main, event("2026-09-16T05:30:00Z", "Palautuu"))
    [package] = build_packages([returned], replace(SETTINGS, max_outgoing_shipments=0), "fi", NOW).packages
    assert (package["direction"], package["status"], package["raw_status"]) == ("incoming", status, main)
    assert package["origin"] == "Maija Mallikas", "from the one it was sent to"
    assert package["recipient"] is None, "it comes to the user"


def test_a_sent_home_delivery_names_its_recipient_once() -> None:
    # A home delivery's destination is a person's name, the recipient's for a sent one, which recipient already holds.
    home = sent("JJFI0702", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa"))
    home["delivery"] = {"destination": {"name": "Maija Mallikas", "city": "TAMPERE"}}
    [package] = build_packages([home], SETTINGS, "fi", NOW).packages
    assert (package["recipient"], package["destination"], package["destination_city"]) == ("Maija Mallikas", None, "TAMPERE")
