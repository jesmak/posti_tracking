"""OmaPosti's API: shipments, renewing the tokens with the device key, and errors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.posti_tracking.api import PostiClient, refused, shipments_in, token_expires_soon
from custom_components.posti_tracking.const import GRAPH_API_URL
from custom_components.posti_tracking.device import Device
from custom_components.posti_tracking.exceptions import PostiAuthError, PostiError

from .conftest import DEVICE_DATA, EXPIRED_TOKENS, SHIPMENTS, VALID_TOKENS, answers, jwt, page_of

DEVICE = Device.from_data(DEVICE_DATA)


def client(hass: HomeAssistant, stored: dict[str, Any], saved: list | None = None) -> PostiClient:
    return PostiClient(hass, DEVICE, stored, saved.append if saved is not None else None)


def test_token_expiry() -> None:
    now = datetime(2026, 9, 16, 9, 0, tzinfo=UTC).timestamp()
    assert not token_expires_soon({"id_token": jwt(datetime(2026, 9, 16, 10, 0, tzinfo=UTC))}, now)
    assert token_expires_soon({"id_token": jwt(datetime(2026, 9, 16, 9, 4, tzinfo=UTC))}, now), "within five minutes"
    assert token_expires_soon({"id_token": "not a token"}, now)
    assert token_expires_soon({}, now)


async def test_shipments_with_valid_tokens(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any) -> None:
    aioclient_mock.post(GRAPH_API_URL, json=page_of(SHIPMENTS))

    assert len(await client(hass, VALID_TOKENS).shipments("fi")) == len(SHIPMENTS)

    [(_method, _url, body, headers)] = aioclient_mock.mock_calls
    assert body["operationName"] == "GetConsumerShipments"
    assert body["variables"] == {"page": 1, "pageSize": 1000, "locale": "fi"}
    # AppSync takes the role token as it is, and the id token in a header of its own.
    assert headers["Authorization"] == "consumer-role"
    assert headers["x-posti-token"] == f"Bearer {VALID_TOKENS['id_token']}"
    renewal.assert_not_called()


async def test_every_page_of_shipments(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any) -> None:
    first, second = SHIPMENTS[:3], SHIPMENTS[3:]
    aioclient_mock.post(GRAPH_API_URL, side_effect=answers((200, page_of(first, 1, 2)), (200, page_of(second, 2, 2))))

    found = await client(hass, VALID_TOKENS).shipments("en")

    assert [shipment["displayId"] for shipment in found] == [shipment["displayId"] for shipment in SHIPMENTS]
    assert [call[2]["variables"] for call in aioclient_mock.mock_calls] == [
        {"page": 1, "pageSize": 1000, "locale": "en"},
        {"page": 2, "pageSize": 1000, "locale": "en"},
    ]


async def test_expiring_tokens_are_renewed_with_the_device(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any) -> None:
    renewal.return_value = {"id_token": "id-2", "access_token": "access-2"}
    aioclient_mock.post(GRAPH_API_URL, json=page_of(SHIPMENTS))
    saved: list[dict] = []
    posti = client(hass, EXPIRED_TOKENS, saved)

    await posti.shipments("fi")

    [(_, device, id_token)] = [call.args for call in renewal.call_args_list]
    assert (device, id_token) == (DEVICE, EXPIRED_TOKENS["id_token"])
    # The refresh and role tokens aren't in the answer, and stay as they were.
    assert posti.tokens == {**EXPIRED_TOKENS, "id_token": "id-2", "access_token": "access-2"}
    assert saved == [posti.tokens]
    [query] = aioclient_mock.mock_calls
    assert query[3]["x-posti-token"] == "Bearer id-2"


UNAUTHORIZED = {
    "data": {"consumerShipments": None},
    "errors": [{"errorType": "Unauthorized", "message": "Not Authorized to access consumerShipments on type Query"}],
}


@pytest.mark.parametrize(("status", "body"), [(401, None), (200, UNAUTHORIZED)])
async def test_tokens_omaposti_refuses_are_renewed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any, status: int, body: Any
) -> None:
    # The role token is refused with 401, the id token with 200 and an Unauthorized error.
    aioclient_mock.post(GRAPH_API_URL, side_effect=answers((status, body), (200, page_of(SHIPMENTS))))

    assert len(await client(hass, VALID_TOKENS).shipments("fi")) == len(SHIPMENTS)
    renewal.assert_called_once()


async def test_renewed_tokens_omaposti_refuses(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any) -> None:
    aioclient_mock.post(GRAPH_API_URL, status=401)
    with pytest.raises(PostiAuthError):
        await client(hass, VALID_TOKENS).shipments("fi")


async def test_a_device_posti_no_longer_trusts(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any) -> None:
    renewal.side_effect = PostiAuthError("deactivated")
    with pytest.raises(PostiAuthError):
        await client(hass, EXPIRED_TOKENS).shipments("fi")


async def test_server_errors_are_not_login_problems(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any) -> None:
    aioclient_mock.post(GRAPH_API_URL, status=503)
    with pytest.raises(PostiError) as error:
        await client(hass, VALID_TOKENS).shipments("fi")
    assert not isinstance(error.value, PostiAuthError)


async def test_a_login_without_a_consumer_role(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any) -> None:
    with pytest.raises(PostiError):
        await client(hass, {**VALID_TOKENS, "role_tokens": [{"type": "business", "token": "x"}]}).shipments("fi")


async def test_an_unexpected_response(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any) -> None:
    aioclient_mock.post(GRAPH_API_URL, json={"errors": [{"message": "Unknown field"}]})
    with pytest.raises(PostiError):
        await client(hass, VALID_TOKENS).shipments("fi")


def test_a_graphql_error_says_what_posti_refused() -> None:
    # A field Posti no longer has fails the whole query, with status 200.
    body = {
        "errors": [
            {
                "errorType": "ValidationError",
                "message": "Validation error of type FieldUndefined: Field 'lockerId' in type 'PickupDetails' is undefined",
            }
        ],
        "data": None,
    }
    with pytest.raises(PostiError, match="lockerId"):
        shipments_in(200, body)


def test_an_answer_without_shipments_or_errors_is_unexpected() -> None:
    with pytest.raises(PostiError, match="unexpected format"):
        shipments_in(200, {"data": {}})


def test_a_token_is_refused_with_401_or_with_a_graphql_error_saying_so() -> None:
    assert refused(401, None)
    assert refused(200, UNAUTHORIZED)
    # One field refused inside an answer that has the shipments is no reason to log in again.
    field = {
        "data": page_of([])["data"],
        "errors": [{"errorType": "Unauthorized", "path": ["consumerShipments", "hits", 0, "pickupPoint"]}],
    }
    assert not refused(200, field)
    assert not refused(200, {"errors": [{"errorType": "ValidationError", "message": "Validation error of type FieldUndefined"}]})
    assert not refused(200, page_of([]))


async def test_a_refusal_on_a_later_page_renews_and_asks_for_that_page_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any
) -> None:
    first, second = SHIPMENTS[:3], SHIPMENTS[3:]
    aioclient_mock.post(GRAPH_API_URL, side_effect=answers((200, page_of(first, 1, 2)), (401, None), (200, page_of(second, 2, 2))))

    found = await client(hass, VALID_TOKENS).shipments("fi")

    assert len(found) == len(SHIPMENTS)
    renewal.assert_called_once()
    assert [call[2]["variables"]["page"] for call in aioclient_mock.mock_calls] == [1, 2, 2]


async def test_an_answer_without_its_page_count_is_one_page(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, renewal: Any) -> None:
    answer = page_of(SHIPMENTS)
    del answer["data"]["consumerShipments"]["totalPages"]
    aioclient_mock.post(GRAPH_API_URL, json=answer)

    assert len(await client(hass, VALID_TOKENS).shipments("fi")) == len(SHIPMENTS)
    assert aioclient_mock.call_count == 1
