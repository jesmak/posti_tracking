"""OmaPosti's API: the shipments of an account, and keeping its tokens valid.

The API, OmaPosti's AppSync GraphQL, accepts the id token of a login together with
the account's consumer role token. Shortly before the id token expires, or when the API refuses it,
the integration's device key renews the tokens (device.refresh). When the
device is no longer trusted, the user has to log in again.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import GRAPH_API_URL, PAGE_SIZE, SHIPMENTS_QUERY, TOKEN_EXPIRY_MARGIN, UNAUTHORIZED_ERROR_TYPE
from .device import Device, jwt_payload, refresh
from .exceptions import PostiAuthError, PostiError

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)


class PostiClient:
    def __init__(
        self,
        hass: HomeAssistant,
        device: Device,
        tokens: Mapping[str, Any],
        on_new_tokens: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._session = async_get_clientsession(hass)
        self._device = device
        self._tokens = dict(tokens)
        # Called with renewed tokens, so that they can be saved for the next start.
        self._on_new_tokens = on_new_tokens

    @property
    def tokens(self) -> dict[str, Any]:
        return self._tokens

    async def shipments(self, locale: str) -> list[dict[str, Any]]:
        """Every shipment of the account, received and sent, page by page, with the event descriptions in the locale."""
        if token_expires_soon(self._tokens, time.time()):
            await self._renew_tokens()
        found: list[dict[str, Any]] = []
        page, pages = 1, 1
        while page <= pages:
            hits, pages = await self._page(page, locale)
            found.extend(hits)
            page += 1
        return found

    async def _page(self, page: int, locale: str) -> tuple[list[dict[str, Any]], int]:
        status, body = await query_shipments(self._session, self._tokens, page, locale)
        if refused(status, body):
            _LOGGER.debug("OmaPosti refused the tokens; renewing them")
            await self._renew_tokens()
            status, body = await query_shipments(self._session, self._tokens, page, locale)
            if refused(status, body):
                raise PostiAuthError("OmaPosti refused the renewed tokens")
        return shipments_in(status, body)

    async def _renew_tokens(self) -> None:
        fresh = await refresh(self._session, self._device, str(self._tokens.get("id_token")))
        # The answer has no refresh token, and may leave out other tokens; those stay as they were.
        self._tokens = {**self._tokens, **fresh}
        if self._on_new_tokens is not None:
            self._on_new_tokens(self._tokens)


async def query_shipments(session: aiohttp.ClientSession, tokens: Mapping[str, Any], page: int, locale: str) -> tuple[int, Any]:
    """Asks OmaPosti for a page of the account's shipments. Returns the status, and the answer when it is 200."""
    role_token = consumer_role_token(tokens)
    if role_token is None:
        raise PostiError("The login has no consumer role")
    try:
        async with session.post(
            GRAPH_API_URL,
            json={
                "operationName": "GetConsumerShipments",
                "variables": {"page": page, "pageSize": PAGE_SIZE, "locale": locale},
                "query": SHIPMENTS_QUERY,
            },
            # AppSync takes the role token as it is, and the id token in a header of its own.
            headers={"Authorization": role_token, "x-posti-token": f"Bearer {tokens.get('id_token')}"},
            timeout=TIMEOUT,
        ) as response:
            if response.status != 200:
                return response.status, None
            return response.status, await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise PostiError(f"OmaPosti couldn't be reached: {err}") from err


def graphql_errors(body: Any) -> list[Mapping[str, Any]]:
    """The errors a GraphQL answer carries. They come with status 200, such as when Posti has dropped a field asked for."""
    errors = body.get("errors") if isinstance(body, dict) else None
    return [error for error in errors if isinstance(error, Mapping)] if isinstance(errors, list) else []


def refused(status: int, body: Any) -> bool:
    """Whether OmaPosti refused the tokens: the role token with status 401, the id token with 200, an Unauthorized
    GraphQL error and no shipments. One field refused inside an answer with shipments is no reason to log in again."""
    if status == 401:
        return True
    data = body.get("data") if isinstance(body, dict) else None
    shipments = data.get("consumerShipments") if isinstance(data, dict) else None
    return shipments is None and any(error.get("errorType") == UNAUTHORIZED_ERROR_TYPE for error in graphql_errors(body))


def shipments_in(status: int, body: Any) -> tuple[list[dict[str, Any]], int]:
    """The shipments of a page, and how many pages there are."""
    if status != 200:
        raise PostiError(f"OmaPosti answered with status {status}")
    data = body.get("data") if isinstance(body, dict) else None
    found = data.get("consumerShipments") if isinstance(data, dict) else None
    hits = found.get("hits") if isinstance(found, dict) else None
    if not isinstance(hits, list):
        errors = graphql_errors(body)
        # Posti's own words say which field or permission it refused, which a bug report needs.
        if errors:
            raise PostiError(f"OmaPosti refused the query: {errors[0].get('message')}")
        raise PostiError("OmaPosti sent the shipments in an unexpected format")
    pages = found.get("totalPages")
    return [hit for hit in hits if isinstance(hit, dict)], pages if isinstance(pages, int) and not isinstance(pages, bool) else 1


def token_expires_soon(tokens: Mapping[str, Any], now: float, margin: float = TOKEN_EXPIRY_MARGIN.total_seconds()) -> bool:
    """Whether the id token has expired or expires within the margin. A token that can't be read counts as expired."""
    payload = jwt_payload(tokens.get("id_token"))
    expiry = payload.get("exp") if payload else None
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
        return True
    return expiry - now < margin


def consumer_role_token(tokens: Mapping[str, Any]) -> str | None:
    for role in tokens.get("role_tokens") or []:
        if isinstance(role, Mapping) and role.get("type") == "consumer" and role.get("token"):
            return str(role["token"])
    return None
