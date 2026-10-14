"""An event entity per account: one event for each thing that happens to a package.

Automations can then act on a package becoming ready for pickup, or being
delivered, without watching the packages attribute themselves.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .changes import EVENT_TYPES, detect_changes
from .const import ATTRIBUTION, CONF_USERNAME
from .coordinator import PostiConfigEntry, PostiCoordinator

# The coordinator fetches for every entity at once, so the entities themselves update nothing.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PostiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PostiPackageEvent(entry.runtime_data)])


class PostiPackageEvent(CoordinatorEntity[PostiCoordinator], EventEntity):
    """The latest thing that happened to a package of the account."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_translation_key = "package"
    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator: PostiCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        username = entry.data[CONF_USERNAME]
        self._attr_unique_id = f"posti_{username}_package"
        self._attr_device_info = coordinator.device_info
        # The packages of the previous update. None until the first one has been seen.
        self._seen: list[dict[str, Any]] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Whatever is already there when Home Assistant starts has not just happened.
        self._seen = self._packages()

    @callback
    def _handle_coordinator_update(self) -> None:
        packages = self._packages()
        changes = detect_changes(self._seen, packages)
        self._seen = packages
        # Written first, so that after a failed update the entity is available again before any event: an event written
        # straight over unavailable looks to an automation like the entity coming back, and is left out.
        super()._handle_coordinator_update()
        for change in changes:
            self._trigger_event(change.event_type, change.attributes)
            # Each event is written on its own, so an automation sees every one of them.
            super()._handle_coordinator_update()

    def _packages(self) -> list[dict[str, Any]]:
        # Every package of the account, so one outside the list, or one that drops out of it and comes back, is seen as
        # what it is.
        return list(self.coordinator.data.every) if self.coordinator.data else []
