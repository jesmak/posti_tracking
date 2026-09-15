"""Logging in like the OmaPosti app: the steps, and reading the login pages."""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from unittest.mock import patch

import pytest
import requests

from custom_components.posti_tracking.exceptions import PostiAuthError, PostiError
from custom_components.posti_tracking.login import authorization, log_in, new_pkce, session_id

from .conftest import PASSWORD, USERNAME, VALID_TOKENS

LOGIN_PAGE = "https://todentaminen.posti.fi/uas/authn/form?_id=session-1&locale=fi"
CALLBACK = "https://auth-service.posti.fi/api/v1/oidc_callback"
SUCCESS_PAGE = f"""<html><body onload="document.forms[0].submit()">
<!-- success.jsp -->
<form method="post" action="{CALLBACK}">
  <input type="hidden" name="code" value="code&amp;3"/>
  <input type="hidden" name="state" value="state-3"/>
</form></body></html>"""


def test_pkce_challenge_is_the_hash_of_the_verifier() -> None:
    pkce = new_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(pkce.verifier.encode()).digest()).decode().rstrip("=")
    assert pkce.challenge == expected
    assert "=" not in pkce.verifier


def test_login_session_from_the_login_page_address() -> None:
    assert session_id(LOGIN_PAGE) == "session-1"
    assert session_id("https://todentaminen.posti.fi/uas/error") is None


def test_authorization_code_from_a_redirect_the_address_or_the_success_page() -> None:
    redirects = ["https://todentaminen.posti.fi/next?code=not-this", f"{CALLBACK}?code=code-1&state=s"]
    assert authorization(redirects, "https://oma.posti.fi/", "").code == "code-1"
    assert authorization([], "https://oma.posti.fi/app/login?code=code-2", "").code == "code-2"

    found = authorization([], "https://todentaminen.posti.fi/uas/success", SUCCESS_PAGE)
    assert found.code == "code&3"
    assert found.callback_url == f"{CALLBACK}?code=code&3&state=state-3"

    assert authorization([], LOGIN_PAGE, "<form>Väärä käyttäjätunnus tai salasana</form>") is None


class FakeResponse:
    def __init__(self, *, body: Any = None, url: str = "", text: str = "", history: list | None = None) -> None:
        self._body = body
        self.url = url
        self.text = text
        self.history = history or []
        self.headers: dict[str, str] = {}
        self.status_code = 200

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


class FakePosti:
    """Posti's login service, as seen through a requests session."""

    def __init__(self, submit_response: FakeResponse) -> None:
        self.submit_response = submit_response
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self) -> FakePosti:
        return self

    def __enter__(self) -> FakePosti:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/login"):
            return FakeResponse(body={"login_url": "https://todentaminen.posti.fi/uas/login?client=omaposti"})
        if "todentaminen.posti.fi/uas/login" in url:
            return FakeResponse(url=LOGIN_PAGE)
        return FakeResponse(url=url)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/submit"):
            return self.submit_response
        return FakeResponse(body=VALID_TOKENS)


def redirect_to(location: str) -> FakeResponse:
    response = FakeResponse()
    response.headers = {"Location": location}
    return response


def test_logging_in() -> None:
    posti = FakePosti(
        FakeResponse(history=[redirect_to(f"{CALLBACK}?code=code-1&state=s")], url="https://oma.posti.fi/")
    )
    with patch("custom_components.posti_tracking.login.requests.Session", posti):
        assert log_in("matti+posti@example.com", PASSWORD) == VALID_TOKENS

    (_, _, start), _, (_, submit_url, submit), (_, _, exchange) = posti.calls
    challenge = start["params"]["code_challenge"]
    assert submit_url == "https://todentaminen.posti.fi/uas/authn/session-1/submit"
    assert submit["data"] == f"username=matti%2Bposti%40example.com&password={PASSWORD}&method=passwordsql"
    verifier = exchange["data"].split("code_verifier=")[1].split("&")[0]
    assert exchange["data"].startswith("code=code-1&")
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=") == challenge


def test_the_success_page_callback_is_visited() -> None:
    posti = FakePosti(FakeResponse(url="https://todentaminen.posti.fi/uas/success", text=SUCCESS_PAGE))
    with patch("custom_components.posti_tracking.login.requests.Session", posti):
        log_in(USERNAME, PASSWORD)
    assert ("GET", f"{CALLBACK}?code=code&3&state=state-3") in [(method, url) for method, url, _ in posti.calls]


def test_wrong_credentials() -> None:
    posti = FakePosti(FakeResponse(url=LOGIN_PAGE, text="<form>Väärä käyttäjätunnus tai salasana</form>"))
    with patch("custom_components.posti_tracking.login.requests.Session", posti), pytest.raises(PostiAuthError):
        log_in(USERNAME, "wrong")


def test_posti_unreachable() -> None:
    posti = FakePosti(FakeResponse())
    posti.get = lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectionError("no route"))  # type: ignore[method-assign]
    with patch("custom_components.posti_tracking.login.requests.Session", posti), pytest.raises(PostiError) as error:
        log_in(USERNAME, PASSWORD)
    assert not isinstance(error.value, PostiAuthError)
