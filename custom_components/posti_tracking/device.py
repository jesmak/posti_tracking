"""The integration as a trusted device of an OmaPosti account.

Right after a login, the integration registers itself as a device with an RSA
key pair of its own, like the OmaPosti app does. From then on, the device key
keeps the login alive: a request signed with it opens the device's vault, which
holds the refresh key that renews the tokens. The device stays trusted until it
is deactivated in the account's device list.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .const import APP_USER_AGENT, AUTH_SERVICE_URL, DEVICE_MODEL, MFA_URL
from .exceptions import PostiAuthError, PostiError
from .login import APP_HEADERS, new_pkce, request_json

PSS = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32)
OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
# Posti's answers, as status 403 or 404, for a device it no longer trusts. The app also treats these as the end of it.
DEVICE_GONE = ("INACTIVE_DEVICE", "NO_DEVICE_FOUND")
# The app accepts only a vault key of this length.
VAULT_KEY_LENGTH = 44

# The last signing time of each device. Posti refuses a signature whose timestamp a device has used already
# (TIMESTAMP_REPLAYED), and timestamps are in seconds, so requests in quick succession must not share one.
last_timestamps: dict[str, int] = {}


@dataclass(frozen=True)
class Device:
    device_id: str
    # The private key as unencrypted PEM: Home Assistant has no secret store to keep it in.
    private_key: str
    # Base64. With the PIN left out, the hash of the salt alone is the device's shared secret.
    salt: str
    # The account, as the sub claim of its id token.
    subject: str

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> Device:
        return cls(data["device_id"], data["private_key"], data["salt"], data["subject"])

    def as_data(self) -> dict[str, str]:
        return asdict(self)

    @property
    def authorization_secret(self) -> str:
        return authorization_secret(base64.b64decode(self.salt))

    def key(self) -> rsa.RSAPrivateKey:
        key = serialization.load_pem_private_key(self.private_key.encode(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise PostiError("The device key isn't an RSA key")
        return key


async def provision(session: aiohttp.ClientSession, tokens: Mapping[str, Any]) -> tuple[Device, dict[str, Any]]:
    """Registers a new device for the account of a fresh login. Returns the device and the login's renewed tokens.

    A login can register only one device: Posti refuses a second one with
    DIRECT_PROVISIONING_AUTHENTICATION_ALREADY_USED.
    """
    id_token = tokens.get("id_token")
    subject = (jwt_payload(id_token) or {}).get("sub")
    if not isinstance(subject, str) or not subject:
        raise PostiError("The login's id token has no account")
    headers = bearer_headers(id_token)

    pkce = new_pkce()
    _, initiated = await request_json(
        session,
        "POST",
        f"{MFA_URL}/provisionings/initiate",
        params={"locale": "fi", "state": str(uuid.uuid4()), "code_challenge": pkce.challenge},
        headers={**headers, "Origin": "https://mfa.posti.fi/"},
    )
    transaction_id = initiated.get("transaction_id")
    finalization_code = initiated.get("finalization_code")
    if not isinstance(transaction_id, str) or not isinstance(finalization_code, str):
        raise PostiError(f"Posti didn't start registering the device: {error_of(initiated)}")

    # Generating the key takes long enough to block the event loop.
    key = await asyncio.get_running_loop().run_in_executor(None, new_key)
    salt = secrets.token_bytes(32)
    secret = authorization_secret(salt)
    public_key = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    status, registered = await request_json(
        session,
        "POST",
        f"{MFA_URL}/provisionings",
        headers=headers,
        json={
            "transaction_id": transaction_id,
            "authorization_secret": secret,
            "code_verifier": pkce.verifier,
            "public_key": public_key.decode(),
            "device_fingerprint": secrets.token_hex(8),
            "device_model": DEVICE_MODEL,
            "enable_mfa": False,
            "finalization_code": finalization_code,
            "signature": sign(key, (secret + transaction_id).encode()),
        },
    )
    device_id = registered.get("device_id")
    if status not in (200, 201) or not isinstance(device_id, str):
        raise PostiError(f"Posti didn't register the device (status {status}): {error_of(registered)}")

    device = Device(
        device_id,
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
        base64.b64encode(salt).decode(),
        subject,
    )
    return device, {**tokens, **await first_refresh(session, device, key, tokens)}


async def first_refresh(
    session: aiohttp.ClientSession, device: Device, key: rsa.RSAPrivateKey, tokens: Mapping[str, Any]
) -> dict[str, Any]:
    """Renews the tokens of the login that registered the device, with its refresh token and a device trust token.

    This is the order in which the proof of the protocol ran before the first vault refresh. Whether the vault
    refresh would also work without it is unknown, so it is kept.
    """
    status, body = await request_json(
        session,
        "POST",
        f"{MFA_URL}/device_trust_token",
        headers=bearer_headers(tokens.get("id_token")),
        json=signed_body(device, key),
    )
    trust_token = body.get("token")
    if status not in (200, 201) or not isinstance(trust_token, str):
        raise PostiError(f"Posti didn't trust the new device (status {status}): {error_of(body)}")

    _, fresh = await request_json(
        session,
        "POST",
        f"{AUTH_SERVICE_URL}/device_refresh",
        headers=APP_HEADERS,
        # Sent as it is, unencoded, like the app sends it.
        data=f"refresh_token={tokens.get('refresh_token')}&device_trust_token={trust_token}&id_token={tokens.get('id_token')}",
    )
    if "error" in fresh or not fresh.get("id_token"):
        raise PostiError(f"Posti didn't renew the tokens of the new device: {error_of(fresh)}")
    return fresh


async def refresh(session: aiohttp.ClientSession, device: Device, id_token: str) -> dict[str, Any]:
    """Renews the tokens with the device key. The answer has new id, access and role tokens, but no refresh token.

    The device's vault holds the refresh key, encrypted for the device. It is fetched and opened anew every time.
    """
    key = device.key()
    status, body = await request_json(
        session, "POST", f"{MFA_URL}/device_refresh", headers=bearer_headers(id_token), json=signed_body(device, key)
    )
    if status == 404 or error_of(body) in DEVICE_GONE:
        raise PostiAuthError(f"The device is no longer trusted, it has been deactivated in OmaPosti ({error_of(body)})")
    trust_token = body.get("device_trust_token")
    vault = body.get("vault_data")
    if status not in (200, 201) or not isinstance(trust_token, str) or not isinstance(vault, str):
        raise PostiError(f"Posti didn't open the device's vault (status {status}): {error_of(body)}")

    _, fresh = await request_json(
        session,
        "POST",
        f"{AUTH_SERVICE_URL}/device_refresh",
        headers=APP_HEADERS,
        # The vault key is base64 and is sent as it is, unencoded, like the app sends it.
        data=f"refresh_token={vault_key(key, vault)}&device_trust_token={trust_token}",
    )
    if "error" in fresh or not fresh.get("id_token"):
        raise PostiError(f"Posti didn't renew the tokens: {error_of(fresh)}")
    return fresh


async def deactivate(session: aiohttp.ClientSession, device: Device, target_id: str, id_token: str) -> None:
    """Removes one device, target_id, from the account's trusted devices, signed by device.

    Never use POST /deactivations without a device: it turns off the account's trusted devices altogether, the phones
    with the OmaPosti app included.
    """
    key = device.key()
    timestamp = next_timestamp(device)
    context = base64.urlsafe_b64encode(hashlib.sha256(f"deprovision_device|{target_id}".encode()).digest())
    status, body = await request_json(
        session,
        "POST",
        f"{MFA_URL}/deactivations/devices/{target_id}",
        headers=bearer_headers(id_token),
        json={
            "acting_device_id": device.device_id,
            "timestamp": timestamp,
            "device_sig": sign(key, f"device|{device.device_id}|{timestamp}".encode()),
            "user_sig": sign(
                key,
                f"user|{device.subject}|{device.device_id}|{timestamp}|{device.authorization_secret}|".encode() + context.rstrip(b"="),
            ),
        },
    )
    if status not in (200, 201):
        raise PostiError(f"Posti didn't deactivate the device (status {status}): {error_of(body)}")


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def authorization_secret(salt: bytes, pin: str = "") -> str:
    return hashlib.sha256(pin.encode() + salt).hexdigest()


def sign(key: rsa.RSAPrivateKey, data: bytes) -> str:
    return base64.b64encode(key.sign(data, PSS, hashes.SHA256())).decode()


def signed_body(device: Device, key: rsa.RSAPrivateKey, timestamp: int | None = None) -> dict[str, Any]:
    """The device's proof of itself: its own signature, and the user's, which binds in the account and the secret."""
    if timestamp is None:
        timestamp = next_timestamp(device)
    return {
        "device_id": device.device_id,
        "device_sig": sign(key, f"device|{device.device_id}|{timestamp}".encode()),
        "timestamp": timestamp,
        "user_sig": sign(key, f"user|{device.subject}|{device.device_id}|{timestamp}|{device.authorization_secret}|".encode()),
    }


def next_timestamp(device: Device) -> int:
    timestamp = max(int(time.time()), last_timestamps.get(device.device_id, 0) + 1)
    last_timestamps[device.device_id] = timestamp
    return timestamp


def vault_key(key: rsa.RSAPrivateKey, vault: str) -> str:
    try:
        plain = key.decrypt(base64.b64decode(vault), OAEP)
        if len(plain) != VAULT_KEY_LENGTH:
            raise PostiError(f"The device's vault held a key of {len(plain)} bytes, not {VAULT_KEY_LENGTH}")
        return plain.decode("ascii")
    except ValueError as err:
        raise PostiError("The device's vault couldn't be opened") from err


def bearer_headers(id_token: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {id_token}", "User-Agent": APP_USER_AGENT}


def jwt_payload(token: Any) -> dict[str, Any] | None:
    """The payload of a JSON web token, unverified: only its expiry and account are read."""
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    part = token.split(".")[1]
    try:
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def error_of(body: Mapping[str, Any]) -> Any:
    error = body.get("error")
    # The MFA service nests its errors: {"error": {"code": 403, "message": "INACTIVE_DEVICE", "translations": ...}}.
    if isinstance(error, Mapping):
        return error.get("message") or error.get("code")
    return error or body.get("message") or body.get("code")
