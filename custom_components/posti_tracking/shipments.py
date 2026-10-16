"""Turning the shipments of an account into the sensor's package list.

The packages are in the format package-tracker-card reads, which other tracking
integrations write too, so the card can list them together.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from .const import (
    CONF_COMPLETED_SHIPMENT_DAYS_SHOWN,
    CONF_MAX_OUTGOING_SHIPMENTS,
    CONF_MAX_SHIPMENTS,
    CONF_PICKUP_CODE,
    CONF_PRIORITIZE_UNDELIVERED,
    CONF_STALE_SHIPMENT_DAY_LIMIT,
    DEFAULT_COMPLETED_SHIPMENT_DAYS_SHOWN,
    DEFAULT_MAX_OUTGOING_SHIPMENTS,
    DEFAULT_MAX_SHIPMENTS,
    DEFAULT_PICKUP_CODE,
    DEFAULT_PRIORITIZE_UNDELIVERED,
    DEFAULT_STALE_SHIPMENT_DAY_LIMIT,
    PICKUP_CODE_CODE,
    PICKUP_CODE_CODE_AND_QR,
    TRACKING_URL,
)

# Package statuses, as package-tracker-card shows them.
STATUS_DELIVERED = 0
STATUS_WAITING = 1
STATUS_RECEIVED = 2
STATUS_IN_TRANSPORT = 3
STATUS_IN_DELIVERY = 4
STATUS_READY_FOR_PICKUP = 5
STATUS_RETURNED_TO_SENDER = 6
STATUS_UNKNOWN = 7

# Posti's status.main. ORDER_RECEIVED is Posti knowing of the shipment before it has it.
STATUS_BY_MAIN = {
    "WAITING": STATUS_WAITING,
    "ORDER_RECEIVED": STATUS_WAITING,
    "RECEIVED": STATUS_RECEIVED,
    "IN_TRANSPORT": STATUS_IN_TRANSPORT,
    "IN_DELIVERY": STATUS_IN_DELIVERY,
    "READY_FOR_PICKUP": STATUS_READY_FOR_PICKUP,
    "DELIVERED": STATUS_DELIVERED,
}
# A shipment on its way back to its sender. For a received one that is the end of it. A sent one comes back to the
# user, so it goes through the statuses again, and its delivery back is its return. While it comes back it is incoming.
STATUS_BY_RETURN = {
    "RETURN_WAITING": STATUS_WAITING,
    "RETURN_IN_TRANSPORT": STATUS_IN_TRANSPORT,
    "RETURN_READY_FOR_PICKUP": STATUS_READY_FOR_PICKUP,
    "RETURN_DELIVERED": STATUS_RETURNED_TO_SENDER,
}
# Delivered and returned packages are finished: they are hidden after the days for delivered packages.
FINISHED = frozenset({STATUS_DELIVERED, STATUS_RETURNED_TO_SENDER})

# Which way a package goes, as package-tracker-card reads it, from Posti's userRole.
DIRECTION_INCOMING = "incoming"
DIRECTION_OUTGOING = "outgoing"
DIRECTION_BY_ROLE = {"RECEIVER": DIRECTION_INCOMING, "SENDER": DIRECTION_OUTGOING}

# A locker outdoors, where the number before lockerId's slash is the keypad to type the code on rather than a door.
LOCKER_OUTDOOR = "LOCKER_OUTDOOR"

# How a package is picked up, from pickupDetails.pickupMethod, as OmaPosti's web app reads it: a code typed in at a
# locker, a pickup code that is also a QR code, or a QR code of the item at a counter shelf.
METHOD_LOCKER_CODE = "LOCKER_CODE"
METHOD_PICKUP_CODE = "PICKUP_CODE"
METHOD_QR_CODE = "QR_CODE"

# Opening hours as one line, in the integration's languages.
DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
DAY_NAMES = {
    "fi": ["ma", "ti", "ke", "to", "pe", "la", "su"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}
ALL_DAY = "24 h"
CLOSED = {"fi": "suljettu", "en": "closed"}


@dataclass(frozen=True)
class PackageSettings:
    prioritize_undelivered: bool
    # The most received and sent packages listed. 0 leaves that direction out of the list, the counts and the events.
    max_shipments: int
    max_outgoing_shipments: int
    # Unfinished packages whose latest event is older are hidden: some stay "in delivery" for good.
    stale_shipment_day_limit: int
    completed_shipment_days_shown: int
    # none, code or code_and_qr: the code opens the locker or collects the package, which not everyone wants on a
    # dashboard. The pickup point itself is always included.
    pickup_code: str

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> PackageSettings:
        return cls(
            prioritize_undelivered=bool(data.get(CONF_PRIORITIZE_UNDELIVERED, DEFAULT_PRIORITIZE_UNDELIVERED)),
            max_shipments=int(data.get(CONF_MAX_SHIPMENTS, DEFAULT_MAX_SHIPMENTS)),
            max_outgoing_shipments=int(data.get(CONF_MAX_OUTGOING_SHIPMENTS, DEFAULT_MAX_OUTGOING_SHIPMENTS)),
            stale_shipment_day_limit=int(data.get(CONF_STALE_SHIPMENT_DAY_LIMIT, DEFAULT_STALE_SHIPMENT_DAY_LIMIT)),
            completed_shipment_days_shown=int(data.get(CONF_COMPLETED_SHIPMENT_DAYS_SHOWN, DEFAULT_COMPLETED_SHIPMENT_DAYS_SHOWN)),
            pickup_code=str(data.get(CONF_PICKUP_CODE, DEFAULT_PICKUP_CODE)),
        )


@dataclass(frozen=True)
class Packages:
    # When a shipment of the account last changed, hidden ones included. None when there are none.
    latest_change: datetime | None
    # The packages to list, newest first and at most the maximum of each direction.
    packages: list[dict[str, Any]]
    # Every package within the age limits, however many: the counts.
    current: list[dict[str, Any]]
    # Every package of the account by Posti's shipment id: what happens to them, also outside the list.
    every: dict[str, dict[str, Any]]


def map_status(main: Any, direction: str | None) -> int:
    """A package status from Posti's status.main, such as READY_FOR_PICKUP, as the user sees it."""
    if main in STATUS_BY_RETURN:
        return STATUS_BY_RETURN[main] if direction == DIRECTION_OUTGOING else STATUS_RETURNED_TO_SENDER
    return STATUS_BY_MAIN.get(main, STATUS_UNKNOWN)


def limit_of(item: Mapping[str, Any], settings: PackageSettings) -> int:
    """The most packages of the item's direction listed. One whose direction isn't known goes with the received ones."""
    return settings.max_outgoing_shipments if item.get("direction") == DIRECTION_OUTGOING else settings.max_shipments


def build_packages(shipments: Iterable[Mapping[str, Any]], settings: PackageSettings, language: str, now: datetime) -> Packages:
    """The packages to list: unfinished ones that aren't stale and recently finished ones, newest first."""
    latest_change: datetime | None = None
    unfinished: list[tuple[datetime, dict[str, Any]]] = []
    finished: list[tuple[datetime, dict[str, Any]]] = []

    newest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for shipment in shipments:
        found = newest_event(shipment.get("events"))
        if found is None:
            continue
        event, changed = found
        item = package(shipment, event, language, settings)
        # A direction whose maximum is 0 is left out altogether: not listed, counted, reported in events or in the time
        # of the latest change.
        if limit_of(item, settings) == 0:
            continue
        if latest_change is None or changed > latest_change:
            latest_change = changed
        keep_newest(newest, identity(shipment, item), changed, item)

    every = {key: item for key, (_, item) in newest.items()}
    for changed, item in newest.values():
        status = item["status"]
        # The limits are whole days of age, so a package two days and an hour old is past a limit of two.
        age = now - changed
        if status not in FINISHED and age <= timedelta(days=settings.stale_shipment_day_limit):
            unfinished.append((changed, item))
        elif status in FINISHED and age <= timedelta(days=settings.completed_shipment_days_shown):
            finished.append((changed, item))

    unfinished.sort(key=change_time, reverse=True)
    finished.sort(key=change_time, reverse=True)
    ordered = unfinished + finished
    if not settings.prioritize_undelivered:
        ordered.sort(key=change_time, reverse=True)
    current = [item for _, item in ordered]
    # Each direction up to its own maximum, so many sent packages don't push the received ones off the list.
    listed: list[dict[str, Any]] = []
    taken = {False: 0, True: 0}
    for item in current:
        sent = item.get("direction") == DIRECTION_OUTGOING
        if taken[sent] < limit_of(item, settings):
            taken[sent] += 1
            listed.append(item)
    return Packages(latest_change, listed, current, every)


def newest_event(events: Any) -> tuple[Mapping[str, Any], datetime] | None:
    """The latest event of a shipment and its time, by the time rather than the order Posti happens to send them in."""
    timed = [(event, parse_time(event.get("timestamp"))) for event in events or [] if isinstance(event, Mapping)]
    timed = [(event, time) for event, time in timed if time is not None]
    return max(timed, key=lambda pair: pair[1]) if timed else None


def identity(shipment: Mapping[str, Any], item: Mapping[str, Any]) -> str | None:
    """What tells a package apart from the others over time: Posti's shipment id, which stays the same when a tracking
    number is added later and so changes the number shown."""
    key = shipment.get("shipmentId") or item.get("shipment_number")
    return str(key) if key else None


def keep_newest(newest: dict[str, tuple[datetime, dict[str, Any]]], key: str | None, changed: datetime, item: dict[str, Any]) -> None:
    """Keeps a package by its key, the newest copy when the account lists one twice. One without a number is left out,
    since nothing would tell its updates apart from another's."""
    if not key:
        return
    if key not in newest or changed > newest[key][0]:
        newest[key] = (changed, item)


def change_time(item: tuple[datetime, dict[str, Any]]) -> datetime:
    return item[0]


def package(shipment: Mapping[str, Any], event: Mapping[str, Any], language: str, settings: PackageSettings) -> dict[str, Any]:
    main = mapping(shipment.get("status")).get("main")
    role = DIRECTION_BY_ROLE.get(shipment.get("userRole"))
    status = map_status(main, role)
    # A sent shipment coming back is on its way to the user, to be collected like any other.
    direction = DIRECTION_INCOMING if role == DIRECTION_OUTGOING and main in STATUS_BY_RETURN else role
    sent = direction == DIRECTION_OUTGOING
    point = mapping(shipment.get("pickupPoint"))
    address = mapping(point.get("address"))
    delivery = mapping(shipment.get("delivery"))
    destination = mapping(delivery.get("destination"))
    measurements = mapping(shipment.get("measurements"))
    shipment_number = shipment.get("displayId") or shipment.get("shipmentId")
    # displayName is the other party: the sender of a received shipment, the recipient of a sent one.
    other = text(shipment.get("displayName"))
    # Only a setting that asks for the code shows it: an unknown value keeps it out, as the code is a secret.
    with_code = settings.pickup_code in (PICKUP_CODE_CODE, PICKUP_CODE_CODE_AND_QR)
    details = mapping(point.get("pickupDetails"))
    return {
        "origin": None if sent else other,
        # Posti no longer tells where a shipment was sent from.
        "origin_city": None,
        # The pickup point when there is one, otherwise where it is delivered, or for a sent one the recipient.
        "destination": text(address.get("publicName")) or text(destination.get("name")) or (other if sent else None),
        "destination_city": text(address.get("city")) or text(destination.get("city")),
        "shipment_number": shipment_number,
        # Nor when it was added to the account.
        "shipment_date": None,
        "status": status,
        "raw_status": main,
        "direction": direction,
        "latest_event": text(event.get("eventDescription")),
        "latest_event_city": text(event.get("city")),
        "latest_event_country": None,
        "latest_event_date": event.get("timestamp"),
        "estimated_delivery": text(mapping(delivery.get("time")).get("timestamp")),
        "pickup_deadline": text(point.get("lastCollectionDate")),
        "weight": number(mapping(measurements.get("weight")).get("value")),
        "package_count": whole_number(mapping(measurements.get("packageQuantity")).get("value")),
        "pickup_point": pickup_point(point, details, language, with_door=with_code),
        "pickup_code": pickup_code(details) if with_code else None,
        "pickup_qr": pickup_qr(details) if settings.pickup_code == PICKUP_CODE_CODE_AND_QR else None,
        "source": "Posti",
        "tracking_url": tracking_url(shipment_number),
    }


def pickup_point(point: Mapping[str, Any], details: Mapping[str, Any], language: str, *, with_door: bool) -> dict[str, Any] | None:
    """Where the package is picked up: the locker or counter, with its address. The locker door or the counter shelf
    goes with the code."""
    address = mapping(point.get("address"))
    found = {
        "name": text(address.get("publicName")),
        "compartment": compartment(point, details) if with_door else None,
        "street": text(address.get("streetAddress")),
        "postal_code": text(address.get("postcode")),
        "city": text(address.get("city")),
        "type": text(point.get("type")),
        "available": opening_hours(point.get("openingHours"), language),
    }
    return found if any(value for value in found.values()) else None


def compartment(point: Mapping[str, Any], details: Mapping[str, Any]) -> str | None:
    """The locker door, the part of lockerId before its slash, or the shelf a counter keeps the package on. At an
    outdoor locker that part is the keypad, not a door, so there is none."""
    method = details.get("pickupMethod")
    if method == METHOD_LOCKER_CODE and point.get("type") != LOCKER_OUTDOOR:
        door = (text(details.get("lockerId")) or "").split("/")[0]
        return door or None
    if method == METHOD_QR_CODE:
        return text(details.get("shelfId"))
    return None


def pickup_code(details: Mapping[str, Any]) -> str | None:
    """The code typed in at a locker, or the pickup code given at a counter. A QR code pickup has none."""
    return text(details.get("pinCode")) if details.get("pickupMethod") in (METHOD_LOCKER_CODE, METHOD_PICKUP_CODE) else None


def pickup_qr(details: Mapping[str, Any]) -> str | None:
    """What the QR code holds, as OmaPosti's web app makes it: the pickup code, or at a counter shelf the item's id.
    Lockers take the code on a keypad, so they have none."""
    method = details.get("pickupMethod")
    if method == METHOD_PICKUP_CODE:
        return text(details.get("pinCode"))
    if method == METHOD_QR_CODE:
        return text(details.get("itemId"))
    return None


def opening_hours(hours: Any, language: str) -> str | None:
    """The opening hours as one line, days with the same hours together: ma-la 7.00 - 21.00, su 10.00 - 18.00."""
    names = DAY_NAMES.get(language, DAY_NAMES["en"])
    by_day = {item.get("dayOfWeek"): item for item in hours or [] if isinstance(item, Mapping)}
    spans: list[tuple[int, int, str]] = []
    for index, day in enumerate(DAYS):
        if day not in by_day:
            continue
        when = day_hours(by_day[day], language)
        if when is None:
            continue
        if spans and spans[-1][1] == index - 1 and spans[-1][2] == when:
            spans[-1] = (spans[-1][0], index, when)
        else:
            spans.append((index, index, when))
    parts = [f"{names[first]}{'' if first == last else '-' + names[last]} {when}" for first, last, when in spans]
    return ", ".join(parts) or None


def day_hours(item: Mapping[str, Any], language: str) -> str | None:
    if item.get("closed") is True:
        return CLOSED.get(language, CLOSED["en"])
    if item.get("open24h") is True:
        return ALL_DAY
    opens, closes = clock(item.get("opens"), language), clock(item.get("closes"), language)
    return f"{opens} - {closes}" if opens and closes else None


def clock(value: Any, language: str) -> str | None:
    """07:00:00 as 7.00 in Finnish and 7:00 in English."""
    parts = str(value or "").split(":")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return f"{int(parts[0])}{'.' if language == 'fi' else ':'}{parts[1]}"


def mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def text(value: Any) -> str | None:
    """A text from Posti, or None for anything else, an empty one included."""
    return value if isinstance(value, str) and value else None


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def whole_number(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_time(value: Any) -> datetime | None:
    """An event time. Posti's times are UTC."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def tracking_url(shipment_number: Any) -> str | None:
    """The carrier's own tracking page for the package."""
    return TRACKING_URL.format(number=quote(str(shipment_number), safe="")) if shipment_number else None
