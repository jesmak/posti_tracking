"""Logging in like the OmaPosti app with strong authentication: the steps, and reading the login pages."""

from __future__ import annotations

import base64
import hashlib

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.posti_tracking.const import AUTH_SERVICE_URL
from custom_components.posti_tracking.exceptions import InvalidReturnAddress, PostiAuthError
from custom_components.posti_tracking.login import (
    StrongLogin,
    authentication_methods,
    bank_address,
    bank_form_address,
    checked_return_address,
    finish_login,
    new_pkce,
    start_login,
)

from .conftest import VALID_TOKENS


def test_pkce_challenge_is_the_hash_of_the_verifier() -> None:
    pkce = new_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(pkce.verifier.encode()).digest()).decode().rstrip("=")
    assert pkce.challenge == expected
    assert "=" not in pkce.verifier


# The pages are cut down to what is read from them, with the same escaping.
SELECT = "&#x2F;uas&#x2F;authn&#x2F;session-1&#x2F;select?entityID=e2b6cc5d&amp;locale=fi&amp;method="
METHODS_PAGE = f"""<html><body>
<a class="idp" href="{SELECT}telia.ftn.oidc.nordea.1"><img src="/img/nordea.png" alt="Nordea"/></a>
<a class="idp" href="{SELECT}telia.ftn.oidc.mobileid.1">Mobiilivarmenne</a>
<a class="idp" href="{SELECT}telia.ftn.oidc.nordea.1">Nordea</a>
<a href="/uas/authn/session-1/cancel">Peruuta</a>
</body></html>"""
BANK_PAGE = """<html><body onload="document.forms[0].submit()">
<form method="get" action="https:&#x2F;&#x2F;tunnistus.telia.fi&#x2F;uas&#x2F;oauth2&#x2F;authorization">
  <input type="hidden" name="request" value="eyJ.request+part/="/>
</form></body></html>"""
RETURN_ADDRESS = "https://todentaminen.posti.fi/uas/return/telia.ftn.oidc.nordea.1/redirect?code=bank-code&state=s1"
RETURN_PAGE = """<html><body onload="document.forms[0].submit()">
<form method="post" action="https://auth-service.posti.fi/api/v1/device_oidc_callback">
  <input type="hidden" name="code" value="eyJ.code"/>
  <input type="hidden" name="state" value="state-1"/>
</form></body></html>"""


def test_authentication_methods_are_read_from_the_login_page() -> None:
    methods = authentication_methods(METHODS_PAGE)

    assert list(methods) == ["telia.ftn.oidc.nordea.1", "telia.ftn.oidc.mobileid.1"]
    assert methods["telia.ftn.oidc.mobileid.1"] == (
        "/uas/authn/session-1/select?entityID=e2b6cc5d&locale=fi&method=telia.ftn.oidc.mobileid.1"
    )
    assert authentication_methods("<html>Palvelu ei ole käytettävissä</html>") == {}


def test_the_bank_address_from_the_hand_over_page() -> None:
    assert bank_form_address(BANK_PAGE) == ("https://tunnistus.telia.fi/uas/oauth2/authorization?request=eyJ.request%2Bpart%2F%3D")
    assert bank_form_address(RETURN_PAGE) is None


@pytest.mark.parametrize(
    "address",
    [
        "",
        "https://oma.posti.fi/app/login?code=x",
        "http://todentaminen.posti.fi/uas/return/x/redirect?code=x",
        "https://todentaminen.posti.fi.example.com/uas/return/x/redirect?code=x",
        "https://todentaminen.posti.fi/uas/authn/session-1/select?code=x",
        "https://todentaminen.posti.fi/uas/return/x/redirect?error=access_denied",
    ],
)
def test_addresses_that_are_not_the_return_address(address: str) -> None:
    with pytest.raises(InvalidReturnAddress):
        checked_return_address(address)


def test_the_return_address_is_taken_as_pasted() -> None:
    assert checked_return_address(f"  {RETURN_ADDRESS}\n") == RETURN_ADDRESS


async def test_the_strong_authentication_login(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    login_url = "https://todentaminen.posti.fi/uas/oauth2/authorization?client_id=e2b6cc5d"
    aioclient_mock.post(f"{AUTH_SERVICE_URL}/device_login", json={"login_url": login_url})
    aioclient_mock.get(login_url, text=METHODS_PAGE)
    aioclient_mock.get(
        f"https://todentaminen.posti.fi{SELECT.replace('&#x2F;', '/').replace('&amp;', '&')}telia.ftn.oidc.mobileid.1",
        text=BANK_PAGE,
    )
    aioclient_mock.get(RETURN_ADDRESS, text=RETURN_PAGE)
    aioclient_mock.get(
        f"{AUTH_SERVICE_URL}/device_oidc_callback?code=eyJ.code&state=state-1",
        status=302,
        headers={"Location": "https://oma.posti.fi/app/login?code=eyJ.final%2Bcode"},
    )
    aioclient_mock.post(f"{AUTH_SERVICE_URL}/token_v2", json=VALID_TOKENS)
    session = async_get_clientsession(hass)

    login = await start_login(session)
    assert list(login.methods) == ["telia.ftn.oidc.nordea.1", "telia.ftn.oidc.mobileid.1"]
    assert (await bank_address(session, login, "telia.ftn.oidc.mobileid.1")).startswith("https://tunnistus.telia.fi/")
    assert await finish_login(session, login, RETURN_ADDRESS) == VALID_TOKENS

    calls = aioclient_mock.mock_calls
    challenge = calls[0][2]["code_challenge"]
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(login.verifier.encode()).digest()).decode().rstrip("=")
    # Posti binds the code to the login only when its pages see the web view's header.
    assert all(headers["X-Requested-With"] == "fi.itella.posti.android" for _, _, _, headers in calls[1:5])
    assert calls[5][2] == f"code=eyJ.final%2Bcode&code_verifier={login.verifier}&grant_type=authorization_code"


async def test_an_authentication_posti_no_longer_accepts(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(RETURN_ADDRESS, text="<html>Istunto on vanhentunut</html>")
    login = StrongLogin("verifier", {"telia.ftn.oidc.nordea.1": "/uas/authn/session-1/select"})

    with pytest.raises(PostiAuthError):
        await finish_login(async_get_clientsession(hass), login, RETURN_ADDRESS)
