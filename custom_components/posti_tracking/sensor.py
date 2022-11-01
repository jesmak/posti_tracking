import logging
from datetime import (timedelta, datetime)
from typing import Any, Callable, Dict, Optional

from aiohttp import ClientError

from homeassistant import config_entries, core
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import (
    ConfigType,
    DiscoveryInfoType,
    HomeAssistantType,
)

from .session import PostiSession
from .const import QUERY_GET_SHIPMENTS, CONF_USERNAME, CONF_PASSWORD, CONF_LANGUAGE, CONF_MAX_SHIPMENTS, \
    CONF_STALE_SHIPMENT_DAY_LIMIT, CONF_COMPLETED_SHIPMENT_DAYS_SHOWN, DOMAIN, CONF_PRIORITIZE_UNDELIVERED

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=10)
ATTRIBUTION = "Data provided by Posti Group Oyj"

ATTR_PACKAGES = "packages"
ATTR_ORIGIN = "origin"
ATTR_ORIGIN_CITY = "origin_city"
ATTR_DESTINATION = "destination"
ATTR_DESTINATION_CITY = "destination_city"
ATTR_SHIPMENT_NUMBER = "shipment_number"
ATTR_SHIPMENT_DATE = "shipment_date"
ATTR_STATUS = "status"
ATTR_RAW_STATUS = "raw_status"
ATTR_LATEST_EVENT = "latest_event"
ATTR_LATEST_EVENT_COUNTRY = "latest_event_country"
ATTR_LATEST_EVENT_CITY = "latest_event_city"
ATTR_LATEST_EVENT_DATE = "latest_event_date"
ATTR_SOURCE = "source"


async def async_setup_platform(
        hass: HomeAssistantType,
        config: ConfigType,
        async_add_entities: Callable,
        discovery_info: Optional[DiscoveryInfoType] = None,
) -> None:
    session = PostiSession(config[CONF_USERNAME], config[CONF_PASSWORD])
    await hass.async_add_executor_job(session.authenticate)
    async_add_entities(
        [PostiSensor(
            session,
            config[CONF_USERNAME],
            config[CONF_LANGUAGE],
            config[CONF_PRIORITIZE_UNDELIVERED],
            config[CONF_MAX_SHIPMENTS],
            config[CONF_STALE_SHIPMENT_DAY_LIMIT],
            config[CONF_COMPLETED_SHIPMENT_DAYS_SHOWN]
        )],
        update_before_add=True
    )


async def async_setup_entry(hass: core.HomeAssistant, config_entry: config_entries.ConfigEntry, async_add_entities):
    config = hass.data[DOMAIN][config_entry.entry_id]
    if config_entry.options:
        config.update(config_entry.options)
    session = PostiSession(config[CONF_USERNAME], config[CONF_PASSWORD])
    await hass.async_add_executor_job(session.authenticate)
    async_add_entities(
        [PostiSensor(
            session,
            config[CONF_USERNAME],
            config[CONF_LANGUAGE],
            config[CONF_PRIORITIZE_UNDELIVERED],
            config[CONF_MAX_SHIPMENTS],
            config[CONF_STALE_SHIPMENT_DAY_LIMIT],
            config[CONF_COMPLETED_SHIPMENT_DAYS_SHOWN]
        )],
        update_before_add=True
    )


class PostiSensor(Entity):
    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:package"
    _attr_native_unit_of_measurement = "packages"

    def __init__(
            self,
            session: PostiSession,
            username: str,
            language: str,
            prioritize_undelivered: bool,
            max_shipments: int,
            stale_shipment_day_limit: int,
            completed_shipment_days_shown: int
    ):
        super().__init__()
        self._session = session
        self._username = username
        self._language = language
        self._prioritize_undelivered = prioritize_undelivered
        self._max_shipments = max_shipments
        self._stale_shipment_day_limit = stale_shipment_day_limit
        self._completed_shipment_days_shown = completed_shipment_days_shown
        self._state = None
        self._available = True
        self._attrs = {}

    @property
    def name(self) -> str:
        return f"posti_{self._username}"

    @property
    def unique_id(self) -> str:
        return f"posti_{self._username}"

    @property
    def available(self) -> bool:
        return self._available

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return self._attrs

    @property
    def state(self) -> Optional[str]:
        return self._state

    async def async_update(self):
        try:
            data = await self.hass.async_add_executor_job(self._session.call_api, QUERY_GET_SHIPMENTS)

            latest_timestamp = None

            delivered_packages = []
            undelivered_packages = []

            for shipment in data['shipment']:

                latest_timestamp = shipment['savedDateTime'] if latest_timestamp is None or shipment['savedDateTime'] > latest_timestamp else latest_timestamp
                latest_event = shipment['events'][-1]
                status = map_raw_status(shipment['shipmentPhase'])
                last_status_change = datetime.fromisoformat(str(latest_event['timestamp']).removesuffix('Z'))
                now = datetime.now()
                delta = now - last_status_change

                if status != 0 and delta.days <= self._stale_shipment_day_limit:
                    add_package(undelivered_packages, shipment, status, latest_event, self._language)
                elif status == 0 and delta.days <= self._completed_shipment_days_shown:
                    add_package(delivered_packages, shipment, status, latest_event, self._language)

            delivered_packages.sort(key=lambda x: x[ATTR_LATEST_EVENT_DATE], reverse=True)
            undelivered_packages.sort(key=lambda x: x[ATTR_LATEST_EVENT_DATE], reverse=True)

            package_data = undelivered_packages + delivered_packages

            if not self._prioritize_undelivered:
                package_data.sort(key=lambda x: x[ATTR_LATEST_EVENT_DATE], reverse=True)

            self._attrs[ATTR_PACKAGES] = package_data[0:min(len(package_data), self._max_shipments)]
            self._available = True
            self._state = latest_timestamp

        except ClientError:
            self._available = False


def add_package(package_data: list, shipment: any, status: int, latest_event: any, language: str):
    package_data.append(
        {
            ATTR_ORIGIN: next(iter([', '.join(value['name']) for value in shipment['parties'] if value['role'] == 'CONSIGNOR']), None),
            ATTR_ORIGIN_CITY: shipment['departure']['city'],
            ATTR_DESTINATION: next(iter([', '.join(value['name']) for value in shipment['parties'] if value['role'] == 'DELIVERY']),
                                   next(iter([', '.join(value['name']) for value in shipment['parties'] if value['role'] == 'CONSIGNEE']), None)),
            ATTR_DESTINATION_CITY: shipment['destination']['city'],
            ATTR_SHIPMENT_NUMBER: next(iter(shipment['trackingNumbers']), shipment['shipmentNumber']),
            ATTR_SHIPMENT_DATE: shipment['savedDateTime'],
            ATTR_STATUS: status,
            ATTR_RAW_STATUS: shipment['shipmentPhase'],
            ATTR_LATEST_EVENT: next(iter([value['value'] for value in latest_event['eventDescription'] if value['lang'] == language]), None),
            ATTR_LATEST_EVENT_CITY: latest_event['eventLocation']['city'],
            ATTR_LATEST_EVENT_COUNTRY: latest_event['eventLocation']['country'],
            ATTR_LATEST_EVENT_DATE: latest_event['timestamp'],
            ATTR_SOURCE: "Posti"
        }
    )


def map_raw_status(raw_status: str) -> int:
    status = 7  # unknown

    if raw_status == "WAITING":
        status = 1
    elif raw_status == "RECEIVED":
        status = 2
    elif raw_status == "IN_TRANSPORT":
        status = 3
    elif raw_status == "IN_DELIVERY":
        status = 4
    elif raw_status == "READY_FOR_PICKUP":
        status = 5
    elif raw_status == "RETURNED_TO_SENDER":
        status = 6
    elif raw_status == "DELIVERED":
        status = 0

    return status
