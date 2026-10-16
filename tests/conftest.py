"""Shared fixtures: an account with its tokens, and OmaPosti with sample shipments."""

from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse

from custom_components.posti_tracking.const import AUTH_SERVICE_URL, GRAPH_API_URL

USERNAME = "matti.meikalainen@example.com"
PASSWORD = "salasana"

# When the tests run.
NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)

TOKEN_URL = f"{AUTH_SERVICE_URL}/token_v2"


def jwt(expires: datetime, **claims: Any) -> str:
    """A JSON web token that expires at the given time, with any other claims. It is never verified."""

    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part({'exp': int(expires.timestamp())} | claims)}.signature"


def tokens(expires: datetime, suffix: str = "") -> dict[str, Any]:
    return {
        "access_token": f"access{suffix}",
        "id_token": jwt(expires),
        "refresh_token": f"refresh{suffix}",
        "role_tokens": [{"type": "business", "token": "business-role"}, {"type": "consumer", "token": "consumer-role"}],
    }


VALID_TOKENS = tokens(datetime(2100, 1, 1, tzinfo=UTC))
EXPIRED_TOKENS = tokens(datetime(2000, 1, 1, tzinfo=UTC))

SETTINGS = {
    "language": "fi",
    "prioritize_undelivered": True,
    "max_shipments": 5,
    "max_outgoing_shipments": 5,
    "stale_shipment_day_limit": 15,
    "completed_shipment_day_shown": 3,
    "pickup_code": "none",
}
# The device is never used for real: the tests replace the renewal that would use its key.
DEVICE_DATA = {"device_id": "device-1", "private_key": "private key", "salt": "c2FsdA==", "subject": "account-1"}
ENTRY_DATA = {"username": USERNAME, **SETTINGS, "device": DEVICE_DATA, "tokens": VALID_TOKENS}
# An entry of 2.x and earlier, which logged in with a password.
PASSWORD_ENTRY_DATA = {"username": USERNAME, "password": PASSWORD, **SETTINGS, "tokens": VALID_TOKENS}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Lets Home Assistant load integrations from custom_components/."""


def event(timestamp: str, description: str, city: str = "LAPPEENRANTA") -> dict[str, Any]:
    """An event, its description in the language the query asked for."""
    return {"city": city, "eventDescription": description, "timestamp": timestamp}


def shipment(number: str, status: str, *events: dict[str, Any], role: str = "RECEIVER", **changes: Any) -> dict[str, Any]:
    """A shipment shaped like OmaPosti's consumerShipments hits: events oldest first, times in UTC."""
    return {
        "shipmentId": f"S-{number}",
        "userRole": role,
        "displayId": number,
        # The other party: the sender of a received shipment, the recipient of a sent one.
        "displayName": "Example Shop Oy",
        "delivery": {"time": None, "destination": {"name": None, "city": "LAPPEENRANTA"}},
        "pickupPoint": None,
        "status": {"main": status},
        "measurements": None,
        "events": list(events),
    } | changes


SHIPMENTS = [
    # Ready for pickup since yesterday morning.
    shipment(
        "JJFI0001",
        "READY_FOR_PICKUP",
        event("2026-09-14T08:00:00Z", "Lähetys on kuljetuksessa", "HELSINKI"),
        event("2026-09-15T07:00:00Z", "Noudettavissa"),
    ),
    # In transport: the latest change of all.
    shipment("JJFI0002", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa")),
    # Delivered two days ago.
    shipment("JJFI0003", "DELIVERED", event("2026-09-14T06:00:00Z", "Toimitettu")),
    # Returned to the sender yesterday: finished, like a delivered one.
    shipment("JJFI0004", "RETURN_DELIVERED", event("2026-09-15T12:00:00Z", "Palautettu")),
    # Delivered six days ago: hidden.
    shipment("JJFI0005", "DELIVERED", event("2026-09-10T06:00:00Z", "Toimitettu")),
    # Stuck in delivery for weeks: hidden.
    shipment("JJFI0006", "IN_DELIVERY", event("2026-08-20T06:00:00Z", "Jakelussa")),
    # Only announced, without events: skipped.
    shipment("JJFI0007", "WAITING"),
    # A status this integration doesn't know yet.
    shipment("JJFI0008", "SOMETHING_NEW", event("2026-09-16T04:00:00Z", "Uusi vaihe")),
]


def page_of(shipments: list[dict[str, Any]], page: int = 1, pages: int = 1) -> dict[str, Any]:
    """OmaPosti's answer: one page of the account's shipments."""
    return {"data": {"consumerShipments": {"page": page, "totalPages": pages, "hits": shipments}}}


def answers(*responses: tuple[int, Any]) -> Callable[..., Awaitable[AiohttpClientMockResponse]]:
    """Answers with the given (status, JSON) responses in turn, repeating the last one."""
    remaining = list(responses)

    async def answer(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        status, body = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return AiohttpClientMockResponse(method, url, status=status, json=body)

    return answer


@pytest.fixture
def omaposti(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """OmaPosti answering with the shipments."""
    aioclient_mock.post(GRAPH_API_URL, json=page_of(SHIPMENTS))
    return aioclient_mock


@pytest.fixture
def renewal() -> Iterator[Any]:
    """Renewing the tokens with the device key, replaced: it gives valid tokens unless a test tells it otherwise."""
    renewed = {key: VALID_TOKENS[key] for key in ("id_token", "access_token", "role_tokens")}
    with patch("custom_components.posti_tracking.api.refresh", return_value=renewed) as refresh:
        yield refresh
