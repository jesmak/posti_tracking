"""The sensor of an account: when its packages last changed, with the packages in its attributes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_USERNAME, DOMAIN
from .coordinator import PostiConfigEntry, PostiCoordinator

ATTR_PACKAGES = "packages"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PostiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([PostiSensor(entry.runtime_data)])


class PostiSensor(CoordinatorEntity[PostiCoordinator], SensorEntity):
    """The time a package of the account last changed, such as when it arrived or was delivered."""

    # The packages change with every event and would fill the database; the state is history enough.
    _unrecorded_attributes = frozenset({ATTR_PACKAGES})
    _attr_attribution = ATTRIBUTION
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:package"

    def __init__(self, coordinator: PostiCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        username = entry.data[CONF_USERNAME]
        # The same unique id as in earlier versions, so the entity keeps its id and history.
        self._attr_unique_id = f"posti_{username}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Posti ({username})",
            manufacturer="Posti",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.latest_change if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_PACKAGES: self.coordinator.data.packages if self.coordinator.data else []}
