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


def jwt(expires: datetime) -> str:
    """A JSON web token that expires at the given time. Only its expiry is ever read."""

    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part({'exp': int(expires.timestamp())})}.signature"


def tokens(expires: datetime, suffix: str = "") -> dict[str, Any]:
    return {
        "access_token": f"access{suffix}",
        "id_token": jwt(expires),
        "refresh_token": f"refresh{suffix}",
        "role_tokens": [{"type": "business", "token": "business-role"}, {"type": "consumer", "token": "consumer-role"}],
    }


VALID_TOKENS = tokens(datetime(2100, 1, 1, tzinfo=UTC))
EXPIRED_TOKENS = tokens(datetime(2000, 1, 1, tzinfo=UTC))

ENTRY_DATA = {
    "username": USERNAME,
    "password": PASSWORD,
    "language": "fi",
    "prioritize_undelivered": True,
    "max_shipments": 5,
    "stale_shipment_day_limit": 15,
    "completed_shipment_day_shown": 3,
    "include_pickup_details": False,
    "tokens": VALID_TOKENS,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Lets Home Assistant load integrations from custom_components/."""


def event(timestamp: str, fi: str, en: str, city: str = "LAPPEENRANTA") -> dict[str, Any]:
    return {
        "eventDescription": [{"lang": "fi", "value": fi}, {"lang": "en", "value": en}],
        "eventLocation": {"city": city, "country": "FI"},
        "timestamp": timestamp,
    }


def shipment(number: str, phase: str, *events: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A shipment shaped like OmaPosti's: events oldest first, times in UTC."""
    return {
        "shipmentNumber": f"S-{number}",
        "parties": [
            {"name": ["Verkkokauppa.com Oyj"], "role": "CONSIGNOR"},
            {"name": ["Matti Meikäläinen"], "role": "CONSIGNEE"},
            {"name": ["K-Market Keskusta", "Pakettiautomaatti"], "role": "DELIVERY"},
        ],
        "departure": {"city": "HELSINKI"},
        "destination": {"city": "LAPPEENRANTA"},
        "trackingNumbers": [number],
        "events": list(events),
        "shipmentPhase": phase,
        "savedDateTime": "2026-09-14T06:00:00Z",
    } | changes


SHIPMENTS = [
    # Ready for pickup since yesterday morning.
    shipment(
        "JJFI0001",
        "READY_FOR_PICKUP",
        event("2026-09-14T08:00:00Z", "Lähetys on kuljetuksessa", "The shipment is in transport", "HELSINKI"),
        event("2026-09-15T07:00:00Z", "Noudettavissa", "Ready for pickup"),
    ),
    # In transport: the latest change of all.
    shipment("JJFI0002", "IN_TRANSPORT", event("2026-09-16T05:30:00Z", "Kuljetuksessa", "In transport")),
    # Delivered two days ago.
    shipment("JJFI0003", "DELIVERED", event("2026-09-14T06:00:00Z", "Toimitettu", "Delivered")),
    # Returned to the sender yesterday: finished, like a delivered one.
    shipment("JJFI0004", "RETURNED_TO_SENDER", event("2026-09-15T12:00:00Z", "Palautettu", "Returned")),
    # Delivered six days ago: hidden.
    shipment("JJFI0005", "DELIVERED", event("2026-09-10T06:00:00Z", "Toimitettu", "Delivered")),
    # Stuck in delivery for weeks: hidden.
    shipment("JJFI0006", "IN_DELIVERY", event("2026-08-20T06:00:00Z", "Jakelussa", "In delivery")),
    # Only announced, without events: skipped.
    shipment("JJFI0007", "WAITING"),
    # A phase this integration doesn't know yet.
    shipment("JJFI0008", "SOMETHING_NEW", event("2026-09-16T04:00:00Z", "Uusi vaihe", "New phase")),
]


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
    aioclient_mock.post(GRAPH_API_URL, json={"data": {"shipment": SHIPMENTS}})
    return aioclient_mock


@pytest.fixture
def login() -> Iterator[Any]:
    """The login, replaced: it returns valid tokens unless a test tells it otherwise."""
    with (
        patch("custom_components.posti_tracking.api.log_in", return_value=VALID_TOKENS) as api_login,
        patch("custom_components.posti_tracking.config_flow.log_in", new=api_login),
    ):
        yield api_login
