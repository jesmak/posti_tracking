"""OmaPosti's API: shipments, renewing the tokens, logging in again, and errors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.posti_tracking.api import PostiClient, token_expires_soon
from custom_components.posti_tracking.const import GRAPH_API_URL
from custom_components.posti_tracking.exceptions import PostiAuthError, PostiError

from .conftest import EXPIRED_TOKENS, PASSWORD, SHIPMENTS, TOKEN_URL, USERNAME, VALID_TOKENS, answers, jwt, tokens


def client(hass: HomeAssistant, stored: dict[str, Any] | None, saved: list | None = None) -> PostiClient:
    return PostiClient(hass, USERNAME, PASSWORD, stored, saved.append if saved is not None else None)


def test_token_expiry() -> None:
    now = datetime(2026, 9, 16, 9, 0, tzinfo=UTC).timestamp()
    assert not token_expires_soon({"id_token": jwt(datetime(2026, 9, 16, 10, 0, tzinfo=UTC))}, now)
    assert token_expires_soon({"id_token": jwt(datetime(2026, 9, 16, 9, 4, tzinfo=UTC))}, now), "within five minutes"
    assert token_expires_soon({"id_token": "not a token"}, now)
    assert token_expires_soon({}, now)


async def test_shipments_with_valid_tokens(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any
) -> None:
    aioclient_mock.post(GRAPH_API_URL, json={"data": {"shipment": SHIPMENTS}})

    assert len(await client(hass, VALID_TOKENS).shipments()) == len(SHIPMENTS)

    [(_method, _url, body, headers)] = aioclient_mock.mock_calls
    assert body["operationName"] == "GetShipments"
    assert headers["Authorization"] == f"Bearer {VALID_TOKENS['id_token']}"
    assert headers["X-Omaposti-Roles"] == "consumer-role"
    login.assert_not_called()


async def test_expiring_tokens_are_renewed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any
) -> None:
    renewed = tokens(datetime(2100, 1, 1, tzinfo=UTC), "-2")
    del renewed["refresh_token"], renewed["role_tokens"]
    aioclient_mock.post(TOKEN_URL, json=renewed)
    aioclient_mock.post(GRAPH_API_URL, json={"data": {"shipment": SHIPMENTS}})
    saved: list[dict] = []
    posti = client(hass, EXPIRED_TOKENS, saved)

    await posti.shipments()

    assert posti.tokens == {**renewed, "refresh_token": "refresh", "role_tokens": EXPIRED_TOKENS["role_tokens"]}
    assert saved == [posti.tokens]
    refresh, query = aioclient_mock.mock_calls
    assert refresh[2] == "refresh_token=refresh&grant_type=refresh_token"
    assert query[3]["Authorization"] == f"Bearer {renewed['id_token']}"
    login.assert_not_called()


async def test_a_new_login_when_renewing_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any
) -> None:
    aioclient_mock.post(TOKEN_URL, status=400, json={"error": "invalid_grant"})
    aioclient_mock.post(GRAPH_API_URL, json={"data": {"shipment": SHIPMENTS}})
    saved: list[dict] = []

    await client(hass, EXPIRED_TOKENS, saved).shipments()

    login.assert_called_once_with(USERNAME, PASSWORD)
    assert saved == [VALID_TOKENS]


async def test_a_new_login_when_there_are_no_tokens(
    hass: HomeAssistant, omaposti: AiohttpClientMocker, login: Any
) -> None:
    await client(hass, None).shipments()
    login.assert_called_once()


async def test_a_new_login_when_omaposti_refuses_the_tokens(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any
) -> None:
    aioclient_mock.post(GRAPH_API_URL, side_effect=answers((401, None), (200, {"data": {"shipment": SHIPMENTS}})))

    assert len(await client(hass, VALID_TOKENS).shipments()) == len(SHIPMENTS)
    login.assert_called_once()


async def test_a_refused_password(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any) -> None:
    login.side_effect = PostiAuthError("wrong password")
    aioclient_mock.post(GRAPH_API_URL, status=401)
    with pytest.raises(PostiAuthError):
        await client(hass, VALID_TOKENS).shipments()


async def test_server_errors_are_not_password_problems(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any
) -> None:
    aioclient_mock.post(GRAPH_API_URL, status=503)
    with pytest.raises(PostiError) as error:
        await client(hass, VALID_TOKENS).shipments()
    assert not isinstance(error.value, PostiAuthError)


async def test_a_login_without_a_consumer_role(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any
) -> None:
    with pytest.raises(PostiError):
        await client(hass, {**VALID_TOKENS, "role_tokens": [{"type": "business", "token": "x"}]}).shipments()


async def test_an_unexpected_response(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: Any) -> None:
    aioclient_mock.post(GRAPH_API_URL, json={"errors": [{"message": "Unknown field"}]})
    with pytest.raises(PostiError):
        await client(hass, VALID_TOKENS).shipments()
