"""OmaPosti's API: shipments, renewing the tokens with the device key, and errors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.posti_tracking.api import PostiClient, token_expires_soon
from custom_components.posti_tracking.const import GRAPH_API_URL
from custom_components.posti_tracking.device import Device
from custom_components.posti_tracking.exceptions import PostiAuthError, PostiError

from .conftest import DEVICE_DATA, EXPIRED_TOKENS, SHIPMENTS, VALID_TOKENS, answers, jwt

DEVICE = Device.from_data(DEVICE_DATA)


def client(hass: HomeAssistant, stored: dict[str, Any], saved: list | None = None) -> PostiClient:
    return PostiClient(hass, DEVICE, stored, saved.append if saved is not None else None)


def test_token_expiry() -> None:
    now = datetime(2026, 9, 16, 9, 0, tzinfo=UTC).timestamp()
    assert not token_expires_soon({"id_token": jwt(datetime(2026, 9, 16, 10, 0, tzinfo=UTC))}, now)
    assert token_expires_soon({"id_token": jwt(datetime(2026, 9, 16, 9, 4, tzinfo=UTC))}, now), "within five minutes"
    assert token_expires_soon({"id_token": "not a token"}, now)
    assert token_expires_soon({}, now)


async def test_shipments_with_valid_tokens(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any
) -> None:
    aioclient_mock.post(GRAPH_API_URL, json={"data": {"shipment": SHIPMENTS}})

    assert len(await client(hass, VALID_TOKENS).shipments()) == len(SHIPMENTS)

    [(_method, _url, body, headers)] = aioclient_mock.mock_calls
    assert body["operationName"] == "GetShipments"
    assert headers["Authorization"] == f"Bearer {VALID_TOKENS['id_token']}"
    assert headers["X-Omaposti-Roles"] == "consumer-role"
    renewal.assert_not_called()


async def test_expiring_tokens_are_renewed_with_the_device(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any
) -> None:
    renewal.return_value = {"id_token": "id-2", "access_token": "access-2"}
    aioclient_mock.post(GRAPH_API_URL, json={"data": {"shipment": SHIPMENTS}})
    saved: list[dict] = []
    posti = client(hass, EXPIRED_TOKENS, saved)

    await posti.shipments()

    [(_, device, id_token)] = [call.args for call in renewal.call_args_list]
    assert (device, id_token) == (DEVICE, EXPIRED_TOKENS["id_token"])
    # The refresh and role tokens aren't in the answer, and stay as they were.
    assert posti.tokens == {**EXPIRED_TOKENS, "id_token": "id-2", "access_token": "access-2"}
    assert saved == [posti.tokens]
    [query] = aioclient_mock.mock_calls
    assert query[3]["Authorization"] == "Bearer id-2"


async def test_tokens_omaposti_refuses_are_renewed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any
) -> None:
    aioclient_mock.post(GRAPH_API_URL, side_effect=answers((401, None), (200, {"data": {"shipment": SHIPMENTS}})))

    assert len(await client(hass, VALID_TOKENS).shipments()) == len(SHIPMENTS)
    renewal.assert_called_once()


async def test_renewed_tokens_omaposti_refuses(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any
) -> None:
    aioclient_mock.post(GRAPH_API_URL, status=401)
    with pytest.raises(PostiAuthError):
        await client(hass, VALID_TOKENS).shipments()


async def test_a_device_posti_no_longer_trusts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any
) -> None:
    renewal.side_effect = PostiAuthError("deactivated")
    with pytest.raises(PostiAuthError):
        await client(hass, EXPIRED_TOKENS).shipments()


async def test_server_errors_are_not_login_problems(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any
) -> None:
    aioclient_mock.post(GRAPH_API_URL, status=503)
    with pytest.raises(PostiError) as error:
        await client(hass, VALID_TOKENS).shipments()
    assert not isinstance(error.value, PostiAuthError)


async def test_a_login_without_a_consumer_role(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any
) -> None:
    with pytest.raises(PostiError):
        await client(hass, {**VALID_TOKENS, "role_tokens": [{"type": "business", "token": "x"}]}).shipments()


async def test_an_unexpected_response(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any) -> None:
    aioclient_mock.post(GRAPH_API_URL, json={"errors": [{"message": "Unknown field"}]})
    with pytest.raises(PostiError):
        await client(hass, VALID_TOKENS).shipments()
