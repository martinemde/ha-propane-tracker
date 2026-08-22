"""Constants for Propane Tracker."""

from homeassistant.const import Platform

DOMAIN = "propane_tracker"

PLATFORMS = [Platform.BUTTON, Platform.DATE, Platform.NUMBER, Platform.SENSOR]

SERVICE_RECORD_DELIVERY = "record_delivery"

ATTR_DELIVERY_DATE = "delivery_date"
ATTR_GALLONS = "gallons"
ATTR_PRICE_PER_GALLON = "price_per_gallon"

EVENT_DELIVERY_RECORDED = f"{DOMAIN}_delivery_recorded"

USAGE_STATISTIC_ID = f"{DOMAIN}:propane_usage"
COST_STATISTIC_ID = f"{DOMAIN}:propane_cost"

STORAGE_KEY = f"{DOMAIN}.ledger"
STORAGE_VERSION = 1

LITERS_PER_US_GALLON = 3.785411784

MAX_GALLONS = 5000.0
MAX_PRICE_PER_GALLON = 100.0
