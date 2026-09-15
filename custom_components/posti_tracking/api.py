"""OmaPosti's API: the shipments of an account, and keeping its tokens valid.

The API accepts the id token of a login together with the account's consumer
role token. Shortly before the id token expires, the refresh token renews the
tokens; when that doesn't work, or the API refuses the tokens, the integration
logs in again with the password.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import APP_USER_AGENT, AUTH_SERVICE_URL, GRAPH_API_URL, SHIPMENTS_QUERY, TOKEN_EXPIRY_MARGIN
from .exceptions import PostiAuthError, PostiError
from .login import log_in

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=20)


class PostiClient:
    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        tokens: Mapping[str, Any] | None,
        on_new_tokens: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._username = username
        self._password = password
        self._tokens = dict(tokens) if tokens else None
        # Called with renewed tokens, so that they can be saved for the next start.
        self._on_new_tokens = on_new_tokens

    @property
    def tokens(self) -> dict[str, Any] | None:
        return self._tokens

    async def shipments(self) -> list[dict[str, Any]]:
        await self._ensure_valid_tokens()
        status, body = await self._query_shipments()
        if status == 401:
            _LOGGER.debug("OmaPosti refused the tokens; logging in again")
            await self._log_in()
            status, body = await self._query_shipments()
            if status == 401:
                raise PostiAuthError("OmaPosti refused the tokens of a new login")
        if status != 200:
            raise PostiError(f"OmaPosti answered with status {status}")

        data = body.get("data") if isinstance(body, dict) else None
        shipments = data.get("shipment") if isinstance(data, dict) else None
        if not isinstance(shipments, list):
            raise PostiError("OmaPosti sent the shipments in an unexpected format")
        return [shipment for shipment in shipments if isinstance(shipment, dict)]

    async def _ensure_valid_tokens(self) -> None:
        if self._tokens is None:
            await self._log_in()
        elif token_expires_soon(self._tokens, time.time()) and not await self._refresh_tokens():
            await self._log_in()

    async def _refresh_tokens(self) -> bool:
        """Renews the tokens with the refresh token. False when that didn't work."""
        refresh_token = (self._tokens or {}).get("refresh_token")
        if not refresh_token:
            return False
        try:
            async with self._session.post(
                f"{AUTH_SERVICE_URL}/token_v2",
                headers={"User-Agent": APP_USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
                data=f"refresh_token={refresh_token}&grant_type=refresh_token",
                timeout=TIMEOUT,
            ) as response:
                body = await response.json(content_type=None) if response.status == 200 else None
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Renewing the tokens failed: %s", err)
            return False
        if not isinstance(body, dict) or "error" in body:
            _LOGGER.debug("Posti didn't renew the tokens")
            return False
        # Tokens missing from the answer, such as the refresh token or the role tokens, stay as they were.
        self._set_tokens({**(self._tokens or {}), **body})
        return True

    async def _log_in(self) -> None:
        self._set_tokens(await self._hass.async_add_executor_job(log_in, self._username, self._password))

    def _set_tokens(self, tokens: dict[str, Any]) -> None:
        self._tokens = tokens
        if self._on_new_tokens is not None:
            self._on_new_tokens(tokens)

    async def _query_shipments(self) -> tuple[int, Any]:
        tokens = self._tokens or {}
        role_token = consumer_role_token(tokens)
        if role_token is None:
            raise PostiError("The login has no consumer role")
        try:
            async with self._session.post(
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


def token_expires_soon(
    tokens: Mapping[str, Any], now: float, margin: float = TOKEN_EXPIRY_MARGIN.total_seconds()
) -> bool:
    """Whether the id token has expired or expires within the margin. A token that can't be read counts as expired."""
    payload = jwt_payload(tokens.get("id_token"))
    expiry = payload.get("exp") if payload else None
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
        return True
    return expiry - now < margin


def jwt_payload(token: Any) -> dict[str, Any] | None:
    """The payload of a JSON web token, unverified: only its expiry is read."""
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    part = token.split(".")[1]
    try:
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def consumer_role_token(tokens: Mapping[str, Any]) -> str | None:
    for role in tokens.get("role_tokens") or []:
        if isinstance(role, Mapping) and role.get("type") == "consumer" and role.get("token"):
            return str(role["token"])
    return None
