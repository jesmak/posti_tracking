"""Posti package tracking: the coming and recently delivered packages of an OmaPosti account.

Each config entry is one account, with a sensor that lists its packages in the
format package-tracker-card shows.
"""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_DEVICE, CONF_PASSWORD, CONF_TOKENS, CONF_USERNAME, DOMAIN
from .coordinator import PostiConfigEntry, PostiCoordinator
from .device import Device, deactivate
from .exceptions import PostiError

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.EVENT, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: PostiConfigEntry) -> bool:
    if CONF_DEVICE not in entry.data:
        # Entries of 2.x and earlier logged in with a password, which Posti no longer accepts.
        raise ConfigEntryAuthFailed("Posti no longer accepts passwords. Log in again with strong authentication.")
    coordinator = PostiCoordinator(hass, entry)
    # A device Posti no longer trusts starts reauthentication; Posti being down retries the setup later.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PostiConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: PostiConfigEntry) -> None:
    """Deactivates the entry's device, so that its key, left behind in backups, can no longer renew the tokens."""
    if CONF_DEVICE not in entry.data:
        return
    device = Device.from_data(entry.data[CONF_DEVICE])
    try:
        await deactivate(async_get_clientsession(hass), device, device.device_id, entry.data[CONF_TOKENS]["id_token"])
    except PostiError as err:
        _LOGGER.warning(
            "Home Assistant couldn't remove itself from the trusted devices of %s (%s). Remove it in the OmaPosti app "
            "under More, Settings, Security settings",
            entry.title,
            err,
        )


async def async_migrate_entry(hass: HomeAssistant, entry: PostiConfigEntry) -> bool:
    """Gives entries of 1.x versions a unique id, the account's user name, and drops the password of 2.x versions."""
    if entry.version != 1:
        return False
    if entry.minor_version < 2:
        hass.config_entries.async_update_entry(
            entry, unique_id=entry.data[CONF_USERNAME].strip().lower(), minor_version=2
        )
    if entry.minor_version < 3:
        # Posti no longer accepts passwords, so there is no reason to keep one. Setting up asks for a new login.
        data = {key: value for key, value in entry.data.items() if key != CONF_PASSWORD}
        hass.config_entries.async_update_entry(entry, data=data, minor_version=3)
    return True
