"""OmaPosti's API: the shipments of an account, and keeping its tokens valid.

The API accepts the id token of a login together with the account's consumer
role token. Shortly before the id token expires, or when the API refuses it,
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

from .const import GRAPH_API_URL, SHIPMENTS_QUERY, TOKEN_EXPIRY_MARGIN
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

    async def shipments(self) -> list[dict[str, Any]]:
        if token_expires_soon(self._tokens, time.time()):
            await self._renew_tokens()
        status, body = await query_shipments(self._session, self._tokens)
        if status == 401:
            _LOGGER.debug("OmaPosti refused the tokens; renewing them")
            await self._renew_tokens()
            status, body = await query_shipments(self._session, self._tokens)
            if status == 401:
                raise PostiAuthError("OmaPosti refused the renewed tokens")
        return shipments_in(status, body)

    async def _renew_tokens(self) -> None:
        fresh = await refresh(self._session, self._device, str(self._tokens.get("id_token")))
        # The answer has no refresh token, and may leave out other tokens; those stay as they were.
        self._tokens = {**self._tokens, **fresh}
        if self._on_new_tokens is not None:
            self._on_new_tokens(self._tokens)


async def query_shipments(session: aiohttp.ClientSession, tokens: Mapping[str, Any]) -> tuple[int, Any]:
    """Asks OmaPosti for the account's shipments. Returns the status, and the answer when it is 200."""
    role_token = consumer_role_token(tokens)
    if role_token is None:
        raise PostiError("The login has no consumer role")
    try:
        async with session.post(
            GRAPH_API_URL,
            json={"operationName": "GetShipments", "variables": {}, "query": SHIPMENTS_QUERY},
            headers={"Authorization": f"Bearer {tokens.get('id_token')}", "X-Omaposti-Roles": role_token},
            timeout=TIMEOUT,
        ) as response:
            if response.status != 200:
                return response.status, None
            return response.status, await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        raise PostiError(f"OmaPosti couldn't be reached: {err}") from err


def shipments_in(status: int, body: Any) -> list[dict[str, Any]]:
    if status != 200:
        raise PostiError(f"OmaPosti answered with status {status}")
    data = body.get("data") if isinstance(body, dict) else None
    shipments = data.get("shipment") if isinstance(data, dict) else None
    if not isinstance(shipments, list):
        raise PostiError("OmaPosti sent the shipments in an unexpected format")
    return [shipment for shipment in shipments if isinstance(shipment, dict)]


def token_expires_soon(
    tokens: Mapping[str, Any], now: float, margin: float = TOKEN_EXPIRY_MARGIN.total_seconds()
) -> bool:
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
