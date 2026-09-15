"""Logging in to OmaPosti like its Android app does.

The app opens Posti's login page in a web view with a PKCE challenge, submits
the user name and password there, and exchanges the authorisation code it gets
back for tokens. This takes the same steps with requests, whose redirects and
cookies behave like the web view's. It blocks, so it runs in an executor.
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

import requests

from .const import (
    APP_PACKAGE,
    APP_USER_AGENT,
    AUTH_SERVICE_URL,
    LOGIN_ENTITY_ID,
    LOGIN_REDIRECT_URI,
    UAS_URL,
    WEB_VIEW_USER_AGENT,
)
from .exceptions import PostiAuthError, PostiError

TIMEOUT_SECONDS = 20
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"

CALLBACK = "auth-service.posti.fi/api/v1/oidc_callback"
SESSION_ID = re.compile(r"_id=(.+?)(?:$|&)")
CODE = re.compile(r"code=([^&]+)")
FORM = re.compile(r"<form[^>]+action=[\"']([^\"']+)[\"'][^>]*>(.*?)</form>", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class Pkce:
    verifier: str
    challenge: str


@dataclass(frozen=True)
class Authorization:
    code: str
    # The app's callback address, visited like the app does when the code comes on the success page.
    callback_url: str | None = None


def log_in(username: str, password: str) -> dict[str, Any]:
    """Logs in, and returns the tokens: access, id and refresh tokens, and the role tokens."""
    pkce = new_pkce()
    try:
        with requests.Session() as session:
            response = session.get(
                f"{AUTH_SERVICE_URL}/login",
                params={
                    "redirect_uri": LOGIN_REDIRECT_URI,
                    "code_challenge": pkce.challenge,
                    "code_challenge_method": "S256",
                    "redirect": "false",
                    "locale": "fi",
                    "mobile": "true",
                },
                headers={"User-Agent": APP_USER_AGENT},
                timeout=TIMEOUT_SECONDS,
            )
            login_url = json_body(response).get("login_url")
            if not login_url:
                raise PostiError("Posti's login service didn't give a login page")

            response = session.get(
                login_url,
                headers={
                    "User-Agent": WEB_VIEW_USER_AGENT,
                    "x-posti-mobile": "android",
                    "X-Requested-With": APP_PACKAGE,
                },
                timeout=TIMEOUT_SECONDS,
            )
            login_session = session_id(response.url)
            if not login_session:
                raise PostiError("Posti's login page didn't start a login")

            response = session.post(
                f"{UAS_URL}/authn/{login_session}/submit",
                params={"entityID": LOGIN_ENTITY_ID, "locale": "fi"},
                headers={
                    "User-Agent": WEB_VIEW_USER_AGENT,
                    "Content-Type": FORM_CONTENT_TYPE,
                    "X-Requested-With": APP_PACKAGE,
                },
                data=credentials_form(username, password),
                timeout=TIMEOUT_SECONDS,
            )
            found = authorization(
                [redirect.headers.get("Location", "") for redirect in response.history], response.url, response.text
            )
            if found is None:
                # With wrong credentials, Posti shows the login page again instead of an authorisation code.
                raise PostiAuthError("Posti didn't accept the user name and password")
            if found.callback_url:
                session.get(found.callback_url, timeout=TIMEOUT_SECONDS, allow_redirects=True)

            response = session.post(
                f"{AUTH_SERVICE_URL}/token_v2",
                headers={"User-Agent": APP_USER_AGENT, "Content-Type": FORM_CONTENT_TYPE},
                # The code is sent as it came, already encoded, like the app sends it.
                data=f"code={found.code}&code_verifier={pkce.verifier}&grant_type=authorization_code",
                timeout=TIMEOUT_SECONDS,
            )
            tokens = json_body(response)
    except requests.RequestException as err:
        raise PostiError(f"Posti couldn't be reached: {err}") from err

    if "error" in tokens or not tokens.get("id_token"):
        raise PostiError(f"Posti didn't give tokens: {tokens.get('error')}")
    return tokens


def new_pkce() -> Pkce:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return Pkce(verifier, challenge)


def credentials_form(username: str, password: str) -> str:
    return f"username={urllib.parse.quote(username)}&password={urllib.parse.quote(password)}&method=passwordsql"


def session_id(url: str) -> str | None:
    """The login session in the login page's address."""
    match = SESSION_ID.search(url)
    return match.group(1) if match else None


def authorization(redirects: list[str], final_url: str, page: str) -> Authorization | None:
    """The authorisation code after submitting the credentials.

    It is in a redirect to the app's callback, in the final address, or in the
    automatically submitted form of the success page. None when it is in none of
    them, which is what wrong credentials look like.
    """
    for location in redirects:
        if CALLBACK in location and (match := CODE.search(location)):
            return Authorization(match.group(1))
    if match := CODE.search(final_url):
        return Authorization(match.group(1))
    if "success.jsp" in page and (form := FORM.search(page)):
        code = hidden_input(form.group(2), "code")
        if code:
            state = hidden_input(form.group(2), "state")
            callback_url = f"{html.unescape(form.group(1))}?code={code}&state={state}" if state else None
            return Authorization(code, callback_url)
    return None


def hidden_input(form: str, name: str) -> str | None:
    match = re.search(rf"<input[^>]+name=[\"']{name}[\"'][^>]+value=[\"']([^\"']+)[\"']", form, re.IGNORECASE)
    return html.unescape(match.group(1)) if match else None


def json_body(response: requests.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as err:
        raise PostiError(f"Posti answered with something other than JSON (status {response.status_code})") from err
    if not isinstance(body, dict):
        raise PostiError("Posti answered in an unexpected format")
    return body
