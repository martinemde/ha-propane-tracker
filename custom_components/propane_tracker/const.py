"""Constants for Propane Tracker."""

from homeassistant.const import Platform

DOMAIN = "propane_tracker"

PLATFORMS = [Platform.BUTTON, Platform.DATE, Platform.NUMBER, Platform.SENSOR]

SERVICE_RECORD_DELIVERY = "record_delivery"

ATTR_DELIVERY_DATE = "delivery_date"
ATTR_GALLONS = "gallons"
ATTR_COST = "cost"
ATTR_PRICE_PER_GALLON = "price_per_gallon"

EVENT_DELIVERY_RECORDED = f"{DOMAIN}_delivery_recorded"

DELIVERED_STATISTIC_ID = f"{DOMAIN}:propane_delivered"
COST_STATISTIC_ID = f"{DOMAIN}:propane_cost"
UNMETERED_STATISTIC_ID = f"{DOMAIN}:propane_unmetered"

# Deliveries used to be published as consumption. They are inflow, so the
# series moved to propane_delivered and this one is cleared during migration.
LEGACY_USAGE_STATISTIC_ID = f"{DOMAIN}:propane_usage"

STORAGE_KEY = f"{DOMAIN}.ledger"
STORAGE_VERSION = 2

LITERS_PER_US_GALLON = 3.785411784

CONF_TANK_CAPACITY = "tank_capacity"
CONF_FILL_PERCENT = "fill_percent"
CONF_METER_ENTITY = "meter_entity"
CONF_METER_UNITS_PER_GALLON = "meter_units_per_gallon"

# A residential tank is filled to 80% of its water capacity, leaving room for
# the liquid to expand. The gauge on the tank reads that same percentage, so a
# freshly filled tank reads 80, not 100.
DEFAULT_TANK_CAPACITY = 250.0
DEFAULT_FILL_PERCENT = 80.0

# 91,502 BTU per US gallon of liquid propane over 3,412.14 BTU per kWh. This
# doubles as the calibration knob for a meter that reads high or low.
DEFAULT_METER_UNITS_PER_GALLON = 26.81

MAX_GALLONS = 5000.0
MAX_COST = 100000.0
MAX_PRICE_PER_GALLON = 100.0
MAX_TANK_CAPACITY = 30000.0
