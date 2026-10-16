"""What happened to the packages between two updates.

The account is polled, so a single update can carry several changes: one package
arrives at a pickup point while another is delivered. Each of them becomes an
event of its own, so an automation sees them all.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .shipments import STATUS_DELIVERED, STATUS_READY_FOR_PICKUP, STATUS_RETURNED_TO_SENDER

EVENT_NEW = "new_package"
EVENT_MOVED = "moved"
EVENT_READY_FOR_PICKUP = "ready_for_pickup"
EVENT_DELIVERED = "delivered"
EVENT_RETURNED = "returned"

EVENT_TYPES = [EVENT_NEW, EVENT_MOVED, EVENT_READY_FOR_PICKUP, EVENT_DELIVERED, EVENT_RETURNED]

# The events a status of its own is worth; everything else is a package moving along.
EVENT_BY_STATUS = {
    STATUS_READY_FOR_PICKUP: EVENT_READY_FOR_PICKUP,
    STATUS_DELIVERED: EVENT_DELIVERED,
    STATUS_RETURNED_TO_SENDER: EVENT_RETURNED,
}

# What an event carries about the package it happened to.
EVENT_KEYS = (
    "shipment_number",
    "status",
    "raw_status",
    "direction",
    "origin",
    "destination",
    "destination_city",
    "latest_event",
    "latest_event_city",
    "latest_event_date",
    "source",
)


@dataclass(frozen=True)
class PackageChange:
    event_type: str
    package: Mapping[str, Any]

    @property
    def attributes(self) -> dict[str, Any]:
        return {key: self.package.get(key) for key in EVENT_KEYS}


def detect_changes(previous: Mapping[str, Mapping[str, Any]] | None, current: Mapping[str, Mapping[str, Any]]) -> list[PackageChange]:
    """The events between the packages seen before and now, both by Posti's shipment number.

    Nothing is reported for the first update: the packages that are already there
    when Home Assistant starts have not just happened. A new package that has
    already reached a status of its own, such as one first seen at its pickup
    point, gets that status's event too, so an automation listening for it hears.
    """
    if previous is None:
        return []

    changes: list[PackageChange] = []
    for key, package in current.items():
        was = previous.get(key)
        if was is None:
            changes.append(PackageChange(EVENT_NEW, package))
            if package.get("status") in EVENT_BY_STATUS:
                changes.append(PackageChange(EVENT_BY_STATUS[package.get("status")], package))
        elif was.get("status") != package.get("status"):
            changes.append(PackageChange(EVENT_BY_STATUS.get(package.get("status"), EVENT_MOVED), package))
        elif was.get("latest_event_date") != package.get("latest_event_date"):
            changes.append(PackageChange(EVENT_MOVED, package))
    return changes
