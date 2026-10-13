"""Logging in to OmaPosti like its Android app does.

The app opens Posti's login page in a web view with a PKCE challenge, the user
authenticates strongly there with their bank or Mobiilivarmenne, and the app
exchanges the authorisation code it gets back for tokens. Here the bank leg
happens in the user's own browser and the integration does the rest: it starts
the login, gives the user the bank's address, and finishes the login with the
address the user lands on. Posti binds the code to the PKCE challenge only when
the requests carry the web view's X-Requested-With header, which a browser can't
send, so the Posti side has to be done here.
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
import secrets
import urllib.parse
from dataclasses import dataclass
from typing import Any

import aiohttp
from yarl import URL

from .const import (
    APP_PACKAGE,
    APP_USER_AGENT,
    AUTH_SERVICE_URL,
    LOGIN_REDIRECT_URI,
    UAS_HOST,
    WEB_VIEW_USER_AGENT,
)
from .exceptions import InvalidReturnAddress, PostiAuthError, PostiError

TIMEOUT_SECONDS = 20
TIMEOUT = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"

CODE = re.compile(r"code=([^&]+)")
QUOTED = re.compile(r"[\"']([^\"']*)[\"']")
BANK_FORM = re.compile(r"<form[^>]+action=[\"']([^\"']*tunnistus\.telia\.fi[^\"']*)[\"']", re.IGNORECASE)

# What the login web view sends with every request to Posti's login pages.
WEB_VIEW_HEADERS = {
    "User-Agent": WEB_VIEW_USER_AGENT,
    "x-posti-mobile": "android",
    "X-Requested-With": APP_PACKAGE,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
APP_HEADERS = {"User-Agent": APP_USER_AGENT, "Content-Type": FORM_CONTENT_TYPE}


@dataclass(frozen=True)
class StrongLogin:
    """A started login. Its session's cookies must last until the login is finished."""

    verifier: str
    # The authentication methods Posti offers, by their id (telia.ftn.oidc.<provider>.1), and the address choosing one.
    methods: dict[str, str]


async def start_login(session: aiohttp.ClientSession) -> StrongLogin:
    """Starts a login and reads the authentication methods Posti offers for it."""
    pkce = new_pkce()
    _, body = await request_json(
        session,
        "POST",
        f"{AUTH_SERVICE_URL}/device_login",
        headers=APP_HEADERS,
        data={
            "redirect_uri": LOGIN_REDIRECT_URI,
            "locale": "fi",
            "code_challenge": pkce.challenge,
            "code_challenge_method": "S256",
        },
    )
    login_url = body.get("login_url")
    if not isinstance(login_url, str):
        raise PostiError("Posti's login service didn't give a login page")

    _, page = await request_page(session, login_url)
    methods = authentication_methods(page)
    if not methods:
        raise PostiError("Posti's login page offered no authentication methods")
    return StrongLogin(pkce.verifier, methods)


async def bank_address(session: aiohttp.ClientSession, login: StrongLogin, method: str) -> str:
    """Chooses an authentication method, and returns the address where the user authenticates with it."""
    if method not in login.methods:
        raise PostiError(f"Posti didn't offer the authentication method {method}")
    _, page = await request_page(session, f"https://{UAS_HOST}{login.methods[method]}")
    address = bank_form_address(page)
    if address is None:
        raise PostiError("Posti's login page didn't hand over to the authentication service")
    return address


async def finish_login(session: aiohttp.ClientSession, login: StrongLogin, return_address: str) -> dict[str, Any]:
    """Finishes the login with the address the user's browser landed on after authenticating, and returns the tokens."""
    return_address = checked_return_address(return_address)
    _, page = await request_page(session, return_address)
    code = hidden_input(page, "code")
    state = hidden_input(page, "state")
    if not code or not state:
        # An address used already, or one of a login that has expired, leads to an error page.
        raise PostiAuthError("Posti didn't accept the authentication")

    callback = (
        f"{AUTH_SERVICE_URL}/device_oidc_callback?code={urllib.parse.quote(code, safe='')}&state={urllib.parse.quote(state, safe='')}"
    )
    location, _ = await request_page(session, callback, follow_redirects=False)
    match = CODE.search(location)
    if not match:
        raise PostiAuthError("Posti didn't give an authorisation code")

    code = urllib.parse.unquote(match.group(1))
    _, tokens = await request_json(
        session,
        "POST",
        f"{AUTH_SERVICE_URL}/token_v2",
        headers=APP_HEADERS,
        data=f"code={urllib.parse.quote(code, safe='')}&code_verifier={login.verifier}&grant_type=authorization_code",
    )
    if "error" in tokens or not tokens.get("id_token"):
        raise PostiAuthError(f"Posti didn't give tokens: {tokens.get('error')}")
    return tokens


def authentication_methods(page: str) -> dict[str, str]:
    """The authentication methods on the login page, by id, with the address choosing each, in the page's order."""
    methods: dict[str, str] = {}
    for quoted in QUOTED.findall(page):
        address = html.unescape(quoted)
        parts = urllib.parse.urlsplit(address)
        if not (parts.path.startswith("/uas/authn/") and parts.path.endswith("/select")):
            continue
        method = urllib.parse.parse_qs(parts.query).get("method", [""])[0]
        if method and method not in methods:
            methods[method] = address
    return methods


def bank_form_address(page: str) -> str | None:
    """The authentication service's address on the page that hands the user over to it."""
    form = BANK_FORM.search(page)
    request = hidden_input(page, "request")
    if form is None or request is None:
        return None
    return f"{html.unescape(form.group(1))}?request={urllib.parse.quote(request, safe='')}"


def checked_return_address(address: str) -> str:
    """The pasted address, when it is one Posti's login returns to from the authentication service."""
    address = address.strip()
    parts = urllib.parse.urlsplit(address)
    if (
        parts.scheme != "https"
        or parts.hostname != UAS_HOST
        or not parts.path.startswith("/uas/return/")
        or "code" not in urllib.parse.parse_qs(parts.query)
    ):
        raise InvalidReturnAddress("The address isn't one Posti's login returns to")
    return address


async def request_page(session: aiohttp.ClientSession, url: str, follow_redirects: bool = True) -> tuple[str, str]:
    """GETs a login page like the web view does. Returns the redirect's target when not following it, and the page."""
    try:
        # Posti's addresses are sent as they are; re-encoding them could change the signed request in them.
        async with session.get(
            URL(url, encoded=True), headers=WEB_VIEW_HEADERS, allow_redirects=follow_redirects, timeout=TIMEOUT
        ) as response:
            if response.status >= 400:
                raise PostiError(f"Posti's login page answered with status {response.status}")
            return response.headers.get("Location", ""), await response.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise PostiError(f"Posti couldn't be reached: {err}") from err


async def request_json(session: aiohttp.ClientSession, method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    """Sends a request to one of Posti's APIs, and returns the status and the JSON object it answered with."""
    try:
        async with session.request(method, url, timeout=TIMEOUT, **kwargs) as response:
            status = response.status
            try:
                body = await response.json(content_type=None)
            except ValueError as err:
                raise PostiError(f"Posti answered with something other than JSON (status {status})") from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise PostiError(f"Posti couldn't be reached: {err}") from err
    if not isinstance(body, dict):
        raise PostiError(f"Posti answered in an unexpected format (status {status})")
    return status, body


@dataclass(frozen=True)
class Pkce:
    verifier: str
    challenge: str


def new_pkce() -> Pkce:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return Pkce(verifier, challenge)


def hidden_input(form: str, name: str) -> str | None:
    match = re.search(rf"<input[^>]+name=[\"']{name}[\"'][^>]+value=[\"']([^\"']+)[\"']", form, re.IGNORECASE)
    return html.unescape(match.group(1)) if match else None
