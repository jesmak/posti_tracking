"""The trusted device: its signatures, opening its vault, and renewing the tokens with it."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.posti_tracking.const import AUTH_SERVICE_URL, MFA_URL
from custom_components.posti_tracking.device import (
    OAEP,
    PSS,
    Device,
    authorization_secret,
    deactivate,
    new_key,
    refresh,
    signed_body,
    vault_key,
)
from custom_components.posti_tracking.exceptions import PostiAuthError, PostiDeviceGone, PostiError

from .conftest import jwt

KEY = new_key()
SALT = bytes(range(32))
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
DEVICE = Device("device-1", PEM.decode(), base64.b64encode(SALT).decode(), "account-1")
# A refresh key like the vault's: 44 characters of base64, with the characters that URL encoding would change.
REFRESH_KEY = "ab+/cdefghijklmnopqrstuvwxyz0123456789ABCDE="
ID_TOKEN = jwt(datetime(2100, 1, 1, tzinfo=UTC))


def verify(signature: str, data: str) -> None:
    KEY.public_key().verify(base64.b64decode(signature), data.encode(), PSS, hashes.SHA256())


def vault(plain: bytes) -> str:
    return base64.b64encode(KEY.public_key().encrypt(plain, OAEP)).decode()


def test_authorization_secret_is_the_hash_of_the_pin_and_the_salt() -> None:
    assert authorization_secret(SALT) == hashlib.sha256(SALT).hexdigest()
    assert authorization_secret(SALT, "1234") == hashlib.sha256(b"1234" + SALT).hexdigest()
    assert DEVICE.authorization_secret == hashlib.sha256(SALT).hexdigest()


def test_signatures_cover_the_device_the_account_and_the_secret() -> None:
    body = signed_body(DEVICE, KEY, 1760000000)

    assert body["device_id"] == "device-1"
    assert body["timestamp"] == 1760000000
    verify(body["device_sig"], "device|device-1|1760000000")
    verify(body["user_sig"], f"user|account-1|device-1|1760000000|{hashlib.sha256(SALT).hexdigest()}|")
    with pytest.raises(InvalidSignature):
        verify(body["device_sig"], "device|device-1|1760000001")


def test_quick_signatures_get_timestamps_of_their_own() -> None:
    timestamps = [signed_body(DEVICE, KEY)["timestamp"] for _ in range(3)]
    assert timestamps[1] == timestamps[0] + 1
    assert timestamps[2] == timestamps[0] + 2


def test_the_device_survives_the_config_entry() -> None:
    restored = Device.from_data(DEVICE.as_data())
    assert restored == DEVICE
    assert restored.key().private_numbers() == KEY.private_numbers()


def test_opening_the_vault() -> None:
    assert vault_key(KEY, vault(REFRESH_KEY.encode())) == REFRESH_KEY
    with pytest.raises(PostiError, match="43 bytes"):
        vault_key(KEY, vault(REFRESH_KEY[:-1].encode()))
    with pytest.raises(PostiError, match="couldn't be opened"):
        vault_key(new_key(), vault(REFRESH_KEY.encode()))


async def test_renewing_the_tokens(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        f"{MFA_URL}/device_refresh",
        json={"device_trust_token": "trust-1", "vault_data": vault(REFRESH_KEY.encode()), "vault_version": 3},
    )
    fresh = {"id_token": "id-2", "access_token": "access-2", "role_tokens": []}
    aioclient_mock.post(f"{AUTH_SERVICE_URL}/device_refresh", json=fresh)

    assert await refresh(async_get_clientsession(hass), DEVICE, ID_TOKEN) == fresh

    (_, _, signed, mfa_headers), (_, _, form, _) = aioclient_mock.mock_calls
    assert mfa_headers["Authorization"] == f"Bearer {ID_TOKEN}"
    verify(signed["device_sig"], f"device|device-1|{signed['timestamp']}")
    # The refresh key goes as it is, not URL encoded, and without an id token.
    assert form == f"refresh_token={REFRESH_KEY}&device_trust_token=trust-1"


@pytest.mark.parametrize(
    ("status", "answer"),
    [
        (403, {"error": {"code": 403, "message": "INACTIVE_DEVICE", "translations": {"en": "Not trusted"}}}),
        (404, {"error": {"code": 404, "message": "NO_DEVICE_FOUND"}}),
        (401, {"error": {"code": 401, "message": "UNAUTHORIZED"}}),
    ],
)
async def test_a_deactivated_device(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int, answer: dict) -> None:
    aioclient_mock.post(f"{MFA_URL}/device_refresh", status=status, json=answer)

    with pytest.raises(PostiAuthError):
        await refresh(async_get_clientsession(hass), DEVICE, ID_TOKEN)


async def test_a_removed_device_is_told_apart_from_a_refused_renewal(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{MFA_URL}/device_refresh", status=404, json={"error": {"code": 404, "message": "NO_DEVICE_FOUND"}})

    with pytest.raises(PostiDeviceGone):
        await refresh(async_get_clientsession(hass), DEVICE, ID_TOKEN)


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refusal_without_json_asks_for_a_new_login(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int) -> None:
    aioclient_mock.post(f"{MFA_URL}/device_refresh", status=status, text="")

    with pytest.raises(PostiAuthError):
        await refresh(async_get_clientsession(hass), DEVICE, ID_TOKEN)


async def test_other_vault_errors_are_not_reauthentication(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{MFA_URL}/device_refresh", status=500, json={"error": "server_error"})

    with pytest.raises(PostiError) as raised:
        await refresh(async_get_clientsession(hass), DEVICE, ID_TOKEN)
    assert not isinstance(raised.value, PostiAuthError)


async def test_deactivating_one_device(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{MFA_URL}/deactivations/devices/device-2", json={"status": "deactivated"})

    await deactivate(async_get_clientsession(hass), DEVICE, "device-2", ID_TOKEN)

    [(_, url, body, headers)] = aioclient_mock.mock_calls
    assert str(url) == f"{MFA_URL}/deactivations/devices/device-2"
    assert headers["Authorization"] == f"Bearer {ID_TOKEN}"
    timestamp = body["timestamp"]
    assert body["acting_device_id"] == "device-1"
    verify(body["device_sig"], f"device|device-1|{timestamp}")
    context = base64.urlsafe_b64encode(hashlib.sha256(b"deprovision_device|device-2").digest()).decode().rstrip("=")
    verify(body["user_sig"], f"user|account-1|device-1|{timestamp}|{DEVICE.authorization_secret}|{context}")


async def test_a_deactivation_posti_refuses(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{MFA_URL}/deactivations/devices/device-1", status=404, json={"error": "NO_DEVICE_FOUND"})

    with pytest.raises(PostiError):
        await deactivate(async_get_clientsession(hass), DEVICE, "device-1", ID_TOKEN)


async def test_a_404_without_a_device_code_is_no_reason_to_log_in_again(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{MFA_URL}/device_refresh", status=404, json={"error": "not_found"})

    with pytest.raises(PostiError) as raised:
        await refresh(async_get_clientsession(hass), DEVICE, ID_TOKEN)
    assert not isinstance(raised.value, PostiAuthError), "an endpoint that moved shouldn't send anyone to their bank"


async def test_a_renewal_auth_service_no_longer_grants_asks_for_a_new_login(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        f"{MFA_URL}/device_refresh",
        json={"device_trust_token": "trust-1", "vault_data": vault(REFRESH_KEY.encode()), "vault_version": 3},
    )
    aioclient_mock.post(f"{AUTH_SERVICE_URL}/device_refresh", status=400, json={"error": "invalid_grant"})

    with pytest.raises(PostiAuthError):
        await refresh(async_get_clientsession(hass), DEVICE, ID_TOKEN)
