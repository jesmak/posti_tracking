"""Posti package tracking: the coming and recently delivered packages of an OmaPosti account.

Each config entry is one account, with a sensor that lists its packages in the
format package-tracker-card shows.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import CONF_USERNAME, DOMAIN
from .coordinator import PostiConfigEntry, PostiCoordinator

PLATFORMS = [Platform.EVENT, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: PostiConfigEntry) -> bool:
    coordinator = PostiCoordinator(hass, entry)
    # A password Posti no longer accepts starts reauthentication; Posti being down retries the setup later.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PostiConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: PostiConfigEntry) -> bool:
    """Gives entries of 1.x versions a unique id: the account's user name."""
    if entry.version != 1:
        return False
    if entry.minor_version < 2:
        hass.config_entries.async_update_entry(
            entry, unique_id=entry.data[CONF_USERNAME].strip().lower(), minor_version=2
        )
    return True
