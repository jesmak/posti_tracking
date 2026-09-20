"""The sensors of an account: when its packages last changed, and how many there are of each kind."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_USERNAME, DOMAIN
from .coordinator import PostiConfigEntry, PostiCoordinator
from .shipments import FINISHED, STATUS_READY_FOR_PICKUP

ATTR_PACKAGES = "packages"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PostiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            PostiSensor(coordinator),
            PostiCountSensor(coordinator, "on_the_way", lambda status: status not in FINISHED),
            PostiCountSensor(coordinator, "ready_for_pickup", lambda status: status == STATUS_READY_FOR_PICKUP),
        ]
    )


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


class PostiCountSensor(CoordinatorEntity[PostiCoordinator], SensorEntity):
    """How many of the account's packages are on their way, or waiting to be picked up."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: PostiCoordinator, key: str, counts: Callable[[Any], bool]) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        username = entry.data[CONF_USERNAME]
        self._counts = counts
        self._attr_translation_key = key
        self._attr_icon = "mdi:package-variant-closed" if key == "on_the_way" else "mdi:package-down"
        self._attr_unique_id = f"posti_{username}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Posti ({username})",
            manufacturer="Posti",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        if data is None:
            return None
        return sum(1 for package in data.packages if self._counts(package.get("status")))
