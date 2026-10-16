"""Fetching the packages of an account every 10 minutes."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PostiClient
from .const import CONF_DEVICE, CONF_LANGUAGE, CONF_TOKENS, CONF_USERNAME, DOMAIN, UPDATE_INTERVAL
from .device import Device
from .exceptions import PostiAuthError, PostiError
from .shipments import Packages, PackageSettings, build_packages

_LOGGER = logging.getLogger(__name__)

type PostiConfigEntry = ConfigEntry[PostiCoordinator]


class PostiCoordinator(DataUpdateCoordinator[Packages]):
    config_entry: PostiConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PostiConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            # Not the title, which is the account's email, so the integration's own update errors don't name the account
            # in logs that end up in bug reports. Home Assistant still logs the title itself when setting up.
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.settings = PackageSettings.from_data(entry.data)
        self.language = entry.data[CONF_LANGUAGE]
        self.client = PostiClient(hass, Device.from_data(entry.data[CONF_DEVICE]), entry.data[CONF_TOKENS], self._save_tokens)

    @property
    def device_info(self) -> DeviceInfo:
        """The account as a device, which all of its entities belong to."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            translation_key="account",
            translation_placeholders={"username": self.config_entry.data[CONF_USERNAME]},
            manufacturer="Posti",
            entry_type=DeviceEntryType.SERVICE,
        )

    @callback
    def _save_tokens(self, tokens: dict[str, Any]) -> None:
        """Keeps renewed tokens, so that the next start can use them.

        The entry has no update listener, so saving the tokens doesn't reload the integration.
        """
        self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, CONF_TOKENS: tokens})

    async def _async_update_data(self) -> Packages:
        try:
            shipments = await self.client.shipments(self.language)
        except PostiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PostiError as err:
            raise UpdateFailed(str(err)) from err
        return build_packages(shipments, self.settings, self.language, dt_util.utcnow())
