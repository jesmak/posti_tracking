"""Constants for the Posti package tracking integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "posti_tracking"

ATTRIBUTION: Final = "Data provided by Posti Group Oyj"

AUTH_SERVICE_URL: Final = "https://auth-service.posti.fi/api/v1"
UAS_URL: Final = "https://todentaminen.posti.fi/uas"
GRAPH_API_URL: Final = "https://oma.posti.fi/graphql/v2"

# The integration logs in like the OmaPosti Android app: its HTTP client for the API, its web view for the login page.
APP_USER_AGENT: Final = "okhttp/4.12.0"
WEB_VIEW_USER_AGENT: Final = (
    "Mozilla/5.0 (Linux; Android 16; sdk_gphone64_x86_64 Build/BE2A.250530.026.F3; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/133.0.6943.137 Mobile Safari/537.36 "
    "OmaPostiAndroid/6.0.23+7405/WebView(133.0.6943.137)"
)
APP_PACKAGE: Final = "fi.itella.posti.android"
LOGIN_REDIRECT_URI: Final = "https://oma.posti.fi/app/login"
# The OmaPosti app in Posti's login service.
LOGIN_ENTITY_ID: Final = "34aaf9ea-e060-4d9d-b9a2-2cc6a0e44a2a"

# Languages of the package event descriptions.
LANGUAGES: Final = ["fi", "en"]

# Config entry data. The keys are those of earlier versions, so existing entries keep working.
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_LANGUAGE: Final = "language"
CONF_PRIORITIZE_UNDELIVERED: Final = "prioritize_undelivered"
CONF_MAX_SHIPMENTS: Final = "max_shipments"
CONF_STALE_SHIPMENT_DAY_LIMIT: Final = "stale_shipment_day_limit"
CONF_COMPLETED_SHIPMENT_DAYS_SHOWN: Final = "completed_shipment_day_shown"
# The tokens of the latest login, saved so that a restart doesn't need a new login.
CONF_INCLUDE_PICKUP_DETAILS: Final = "include_pickup_details"

CONF_TOKENS: Final = "tokens"

DEFAULT_PRIORITIZE_UNDELIVERED: Final = True
# The pickup point and its code are left out unless asked for: the code opens the locker.
DEFAULT_INCLUDE_PICKUP_DETAILS: Final = False
DEFAULT_MAX_SHIPMENTS: Final = 5
DEFAULT_STALE_SHIPMENT_DAY_LIMIT: Final = 15
DEFAULT_COMPLETED_SHIPMENT_DAYS_SHOWN: Final = 3

UPDATE_INTERVAL: Final = timedelta(minutes=10)
# The tokens are renewed when the id token expires within this.
TOKEN_EXPIRY_MARGIN: Final = timedelta(minutes=5)

SHIPMENTS_QUERY: Final = """query GetShipments {
  shipment {
    ...ShipmentFields
  }
}

fragment ShipmentFields on shipment {
  shipmentNumber
  parties {
    name
    role
  }
  departure {
    city
  }
  destination {
    city
  }
  trackingNumbers
  events {
    eventDescription {
      lang
      value
    }
    eventLocation {
      city
      country
    }
    timestamp
  }
  shipmentPhase
  savedDateTime
  estimatedDeliveryTime
  grossWeight
  packageQuantity
  pickupPoint {
    type
    lockerAddress
    lockerCode
    pupCode
    availabilityTime
    location {
      street1
      postCode
      city
    }
  }
}
"""
