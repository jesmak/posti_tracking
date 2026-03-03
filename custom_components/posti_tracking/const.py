DOMAIN = "posti_tracking"
AUTH_SERVICE_BASE_URL = "https://auth-service.posti.fi/api/v1"
UAS_BASE_URL = "https://todentaminen.posti.fi/uas"
GRAPH_API_URL = "https://oma.posti.fi/graphql/v2"
# Mobile app User-Agent for WebView requests
USER_AGENT = "Mozilla/5.0 (Linux; Android 16; sdk_gphone64_x86_64 Build/BE2A.250530.026.F3; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/133.0.6943.137 Mobile Safari/537.36 OmaPostiAndroid/6.0.23+7405/WebView(133.0.6943.137)"
LANGUAGES = ["fi", "en"]
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_LANGUAGE = "language"
CONF_PRIORITIZE_UNDELIVERED = "prioritize_undelivered"
CONF_MAX_SHIPMENTS = "max_shipments"
CONF_STALE_SHIPMENT_DAY_LIMIT = "stale_shipment_day_limit"
CONF_COMPLETED_SHIPMENT_DAYS_SHOWN = "completed_shipment_day_shown"
QUERY_GET_SHIPMENTS = """
{
  "operationName": "GetShipments",
  "variables": {},
  "query": "query GetShipments {\\n  shipment {\\n    ...ShipmentFields\\n  }\\n}\\n\\nfragment ShipmentFields on shipment {\\n  shipmentNumber\\n  parties {\\n    name\\n    role\\n  }\\n  departure {\\n    city\\n  }\\n  destination {\\n    city\\n  }\\n  trackingNumbers\\n  events {\\n    eventDescription {\\n      lang\\n      value\\n    }\\n    eventLocation {\\n      city\\n      country\\n    }\\n    timestamp\\n  }\\n  shipmentPhase\\n  savedDateTime\\n}\\n"
}
"""