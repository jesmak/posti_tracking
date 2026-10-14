"""Turning the shipments of an account into the sensor's package list.

The packages are in the format package-tracker-card reads, which other tracking
integrations write too, so the card can list them together.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from .const import (
    CONF_COMPLETED_SHIPMENT_DAYS_SHOWN,
    CONF_MAX_SHIPMENTS,
    CONF_PICKUP_CODE,
    CONF_PRIORITIZE_UNDELIVERED,
    CONF_STALE_SHIPMENT_DAY_LIMIT,
    DEFAULT_COMPLETED_SHIPMENT_DAYS_SHOWN,
    DEFAULT_MAX_SHIPMENTS,
    DEFAULT_PICKUP_CODE,
    DEFAULT_PRIORITIZE_UNDELIVERED,
    DEFAULT_STALE_SHIPMENT_DAY_LIMIT,
    PICKUP_CODE_CODE,
    PICKUP_CODE_CODE_AND_QR,
    QR_POINT_TYPES,
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

STATUS_BY_PHASE = {
    "WAITING": STATUS_WAITING,
    "RECEIVED": STATUS_RECEIVED,
    "IN_TRANSPORT": STATUS_IN_TRANSPORT,
    "IN_DELIVERY": STATUS_IN_DELIVERY,
    "READY_FOR_PICKUP": STATUS_READY_FOR_PICKUP,
    "RETURNED_TO_SENDER": STATUS_RETURNED_TO_SENDER,
    "DELIVERED": STATUS_DELIVERED,
}
# Delivered and returned packages are finished: they are hidden after the days for delivered packages.
FINISHED = frozenset({STATUS_DELIVERED, STATUS_RETURNED_TO_SENDER})


@dataclass(frozen=True)
class PackageSettings:
    prioritize_undelivered: bool
    max_shipments: int
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
            stale_shipment_day_limit=int(data.get(CONF_STALE_SHIPMENT_DAY_LIMIT, DEFAULT_STALE_SHIPMENT_DAY_LIMIT)),
            completed_shipment_days_shown=int(data.get(CONF_COMPLETED_SHIPMENT_DAYS_SHOWN, DEFAULT_COMPLETED_SHIPMENT_DAYS_SHOWN)),
            pickup_code=str(data.get(CONF_PICKUP_CODE, DEFAULT_PICKUP_CODE)),
        )


@dataclass(frozen=True)
class Packages:
    # When a shipment of the account last changed, hidden ones included. None when there are none.
    latest_change: datetime | None
    # The packages to list, newest first and at most the maximum.
    packages: list[dict[str, Any]]
    # Every package within the age limits, however many: the counts.
    current: list[dict[str, Any]]
    # Every package of the account: what happens to them, also outside the list.
    every: list[dict[str, Any]]


def map_raw_status(phase: Any) -> int:
    """A package status from Posti's shipment phase, such as READY_FOR_PICKUP."""
    return STATUS_BY_PHASE.get(phase, STATUS_UNKNOWN)


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
        if latest_change is None or changed > latest_change:
            latest_change = changed

        status = map_raw_status(shipment.get("shipmentPhase"))
        keep_newest(newest, changed, package(shipment, event, status, language, settings))

    every = [item for _, item in newest.values()]
    for changed, item in newest.values():
        status = item["status"]
        age_days = (now - changed).days
        if status not in FINISHED and age_days <= settings.stale_shipment_day_limit:
            unfinished.append((changed, item))
        elif status in FINISHED and age_days <= settings.completed_shipment_days_shown:
            finished.append((changed, item))

    unfinished.sort(key=change_time, reverse=True)
    finished.sort(key=change_time, reverse=True)
    ordered = unfinished + finished
    if not settings.prioritize_undelivered:
        ordered.sort(key=change_time, reverse=True)
    current = [item for _, item in ordered]
    return Packages(latest_change, current[: settings.max_shipments], current, every)


def newest_event(events: Any) -> tuple[Mapping[str, Any], datetime] | None:
    """The latest event of a shipment and its time, by the time rather than the order Posti happens to send them in."""
    timed = [(event, parse_time(event.get("timestamp"))) for event in events or [] if isinstance(event, Mapping)]
    timed = [(event, time) for event, time in timed if time is not None]
    return max(timed, key=lambda pair: pair[1]) if timed else None


def keep_newest(newest: dict[str, tuple[datetime, dict[str, Any]]], changed: datetime, item: dict[str, Any]) -> None:
    """Keeps a package by its number, the newest copy when the account lists one twice. One without a number is left out,
    since nothing would tell its updates apart from another's."""
    number = item.get("shipment_number")
    if not number:
        return
    if number not in newest or changed > newest[number][0]:
        newest[number] = (changed, item)


def change_time(item: tuple[datetime, dict[str, Any]]) -> datetime:
    return item[0]


def package(
    shipment: Mapping[str, Any],
    event: Mapping[str, Any],
    status: int,
    language: str,
    settings: PackageSettings,
) -> dict[str, Any]:
    parties = [party for party in shipment.get("parties") or [] if isinstance(party, Mapping)]
    location = event.get("eventLocation") if isinstance(event.get("eventLocation"), Mapping) else {}
    tracking_numbers = shipment.get("trackingNumbers") or []
    point = shipment.get("pickupPoint") if isinstance(shipment.get("pickupPoint"), Mapping) else {}
    shipment_number = (tracking_numbers[0] if tracking_numbers else None) or shipment.get("shipmentNumber")
    # Only a setting that asks for the code shows it: an unknown value keeps it out, as the code is a secret.
    with_code = settings.pickup_code in (PICKUP_CODE_CODE, PICKUP_CODE_CODE_AND_QR)
    code = pickup_code(point) if with_code else None
    return {
        "origin": party_name(parties, "CONSIGNOR"),
        "origin_city": city(shipment.get("departure")),
        # The pickup point when there is one, otherwise the receiver.
        "destination": party_name(parties, "DELIVERY") or party_name(parties, "CONSIGNEE"),
        "destination_city": city(shipment.get("destination")),
        "shipment_number": shipment_number,
        "shipment_date": shipment.get("savedDateTime"),
        "status": status,
        "raw_status": shipment.get("shipmentPhase"),
        "latest_event": description(event, language),
        "latest_event_city": location.get("city"),
        "latest_event_country": location.get("country"),
        "latest_event_date": event.get("timestamp"),
        "estimated_delivery": shipment.get("estimatedDeliveryTime"),
        # Posti doesn't tell how long a package is kept.
        "pickup_deadline": None,
        "weight": number(shipment.get("grossWeight")),
        "package_count": whole_number(shipment.get("packageQuantity")),
        "pickup_point": pickup_point(point, with_door=with_code),
        "pickup_code": code,
        "pickup_qr": pickup_qr(point, code) if settings.pickup_code == PICKUP_CODE_CODE_AND_QR else None,
        "source": "Posti",
        "tracking_url": tracking_url(shipment_number),
    }


def pickup_point(point: Mapping[str, Any], *, with_door: bool) -> dict[str, Any] | None:
    """Where the package is picked up: the locker or counter, with its address. The locker door goes with the code."""
    location = point.get("location") if isinstance(point.get("location"), Mapping) else {}
    found = {
        "name": point.get("lockerAddress"),
        # The door the package is behind, at lockers with a keypad on every door. Posti flags when it is worth showing:
        # the ID of a package kept at a counter isn't.
        "compartment": point.get("lockerID") if with_door and point.get("showLockerID") is True else None,
        "street": location.get("street1"),
        "postal_code": location.get("postCode"),
        "city": location.get("city"),
        "type": point.get("type"),
        "available": point.get("availabilityTime"),
    }
    return found if any(value for value in found.values()) else None


def pickup_code(point: Mapping[str, Any]) -> str | None:
    """The code that opens the locker, or the one given at a counter."""
    code = point.get("lockerCode") or point.get("pupCode")
    return str(code) if code else None


def pickup_qr(point: Mapping[str, Any], code: str | None) -> str | None:
    """What a QR code for the counter holds: the pickup code as it is shown, at the pickup points that scan one.

    Posti's notices of arrival encode the code people see, such as P12345, whichever field it comes in. A counter
    package can have a pupCode of another kind beside it, a long number, which is not it."""
    return code if code and point.get("type") in QR_POINT_TYPES else None


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


def party_name(parties: list[Mapping[str, Any]], role: str) -> str | None:
    for party in parties:
        if party.get("role") == role:
            name = party.get("name")
            return ", ".join(name) if isinstance(name, list) else name
    return None


def city(place: Any) -> str | None:
    return place.get("city") if isinstance(place, Mapping) else None


def description(event: Mapping[str, Any], language: str) -> str | None:
    """The event's description in the chosen language, or in another when Posti has none in it."""
    descriptions = [item for item in event.get("eventDescription") or [] if isinstance(item, Mapping)]
    for item in descriptions:
        if item.get("lang") == language:
            return item.get("value")
    return descriptions[0].get("value") if descriptions else None


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
