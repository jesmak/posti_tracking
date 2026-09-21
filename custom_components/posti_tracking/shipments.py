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
    CONF_INCLUDE_PICKUP_DETAILS,
    CONF_MAX_SHIPMENTS,
    CONF_PRIORITIZE_UNDELIVERED,
    CONF_STALE_SHIPMENT_DAY_LIMIT,
    DEFAULT_COMPLETED_SHIPMENT_DAYS_SHOWN,
    DEFAULT_INCLUDE_PICKUP_DETAILS,
    DEFAULT_MAX_SHIPMENTS,
    DEFAULT_PRIORITIZE_UNDELIVERED,
    DEFAULT_STALE_SHIPMENT_DAY_LIMIT,
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
    # The pickup point and the code that opens the locker, which not everyone wants on a dashboard.
    include_pickup_details: bool

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> PackageSettings:
        return cls(
            prioritize_undelivered=bool(data.get(CONF_PRIORITIZE_UNDELIVERED, DEFAULT_PRIORITIZE_UNDELIVERED)),
            max_shipments=int(data.get(CONF_MAX_SHIPMENTS, DEFAULT_MAX_SHIPMENTS)),
            stale_shipment_day_limit=int(data.get(CONF_STALE_SHIPMENT_DAY_LIMIT, DEFAULT_STALE_SHIPMENT_DAY_LIMIT)),
            completed_shipment_days_shown=int(
                data.get(CONF_COMPLETED_SHIPMENT_DAYS_SHOWN, DEFAULT_COMPLETED_SHIPMENT_DAYS_SHOWN)
            ),
            include_pickup_details=bool(data.get(CONF_INCLUDE_PICKUP_DETAILS, DEFAULT_INCLUDE_PICKUP_DETAILS)),
        )


@dataclass(frozen=True)
class Packages:
    # When a shipment of the account last changed, hidden ones included. None when there are none.
    latest_change: datetime | None
    packages: list[dict[str, Any]]


def map_raw_status(phase: Any) -> int:
    """A package status from Posti's shipment phase, such as READY_FOR_PICKUP."""
    return STATUS_BY_PHASE.get(phase, STATUS_UNKNOWN)


def build_packages(
    shipments: Iterable[Mapping[str, Any]], settings: PackageSettings, language: str, now: datetime
) -> Packages:
    """The packages to list: unfinished ones that aren't stale and recently finished ones, newest first."""
    latest_change: datetime | None = None
    unfinished: list[tuple[datetime, dict[str, Any]]] = []
    finished: list[tuple[datetime, dict[str, Any]]] = []

    for shipment in shipments:
        events = shipment.get("events")
        if not isinstance(events, list) or not events or not isinstance(events[-1], Mapping):
            continue
        event = events[-1]
        changed = parse_time(event.get("timestamp"))
        if changed is None:
            continue
        if latest_change is None or changed > latest_change:
            latest_change = changed

        status = map_raw_status(shipment.get("shipmentPhase"))
        age_days = (now - changed).days
        if status not in FINISHED and age_days <= settings.stale_shipment_day_limit:
            unfinished.append((changed, package(shipment, event, status, language, settings)))
        elif status in FINISHED and age_days <= settings.completed_shipment_days_shown:
            finished.append((changed, package(shipment, event, status, language, settings)))

    unfinished.sort(key=change_time, reverse=True)
    finished.sort(key=change_time, reverse=True)
    ordered = unfinished + finished
    if not settings.prioritize_undelivered:
        ordered.sort(key=change_time, reverse=True)
    return Packages(latest_change, [item for _, item in ordered[: settings.max_shipments]])


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
        "pickup_point": pickup_point(point) if settings.include_pickup_details else None,
        "pickup_code": pickup_code(point) if settings.include_pickup_details else None,
        "source": "Posti",
        "tracking_url": tracking_url(shipment_number),
    }


def pickup_point(point: Mapping[str, Any]) -> dict[str, Any] | None:
    """Where the package is picked up: the locker or counter, with its address."""
    location = point.get("location") if isinstance(point.get("location"), Mapping) else {}
    found = {
        "name": point.get("lockerAddress"),
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
