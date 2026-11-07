"""Constants for the Orkli Wifi Thermostat integration."""

DOMAIN = "orkli_wifi_thermostat"

DEFAULT_SCAN_INTERVAL = 15
MIN_SCAN_INTERVAL = 15

# The controller can spend several seconds replying across all zones, and
# incoming feedback restarts Home Assistant's refresh timer.
MIN_ZONE_FEEDBACK_TIMEOUT = 60 * 60
