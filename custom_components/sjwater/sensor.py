from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolume
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import homeassistant.util.dt as dt_util

from .const import DOMAIN, entry_id_slug


def _local_timestamp(timestamp: datetime | None) -> datetime | None:
    """Convert a UTC timestamp to Home Assistant's configured time zone."""
    if timestamp is None:
        return None
    return dt_util.as_local(timestamp)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the sensor platform from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities([
        SJWaterHubSensor(coordinator),
        SJWaterHubDailySensor(coordinator)
    ])

class SJWaterHubSensor(CoordinatorEntity, SensorEntity):
    """Representation of the Water Meter."""

    _attr_has_entity_name = True
    _attr_name = "Water Meter Total"
    _attr_device_class = SensorDeviceClass.WATER
    # TOTAL_INCREASING tells HA this is a monotonically increasing meter.
    # Any decrease is treated as a counter reset, never as negative usage.
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.GALLONS
    _attr_icon = "mdi:water"

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        
        acct = entry_id_slug(coordinator.entry.entry_id)
        self.entity_id = f"sensor.sjwater_{acct}_water_usage"
        self._attr_unique_id = f"sjwater_{acct}_water_usage"

    @property
    def device_info(self):
        """Return device information to link entities up."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.entry.entry_id)},
            "name": "SJ Water Hub",
            "manufacturer": "SJ Water Company",
            "model": "Scraped Hub",
        }

    @property
    def native_value(self):
        """Return the continuously accumulating total meter reading."""
        if self.coordinator.data is not None:
            # We use the running sum calculated during statistic compilation
            # which ensures Home Assistant natively never sees a drop in value
            # avoiding artificial negative statistics in the Energy dashboard!
            return max(0.0, self.coordinator.data.get("current_sum", 0.0))
        return None

    @property
    def extra_state_attributes(self):
        """Include reading and successful-update timestamps."""
        if self.coordinator.data is not None:
            return {
                "recorded_at": _local_timestamp(self.coordinator.data.get("timestamp")),
                "data_updated_at": _local_timestamp(
                    self.coordinator.data.get("data_updated_at")
                ),
                "last_successful_update": _local_timestamp(
                    self.coordinator.data.get("last_successful_update")
                ),
            }
        return {}


class SJWaterHubDailySensor(CoordinatorEntity, SensorEntity):
    """Representation of Today's Water Usage."""

    _attr_has_entity_name = True
    _attr_name = "Today's Water Usage"
    _attr_device_class = SensorDeviceClass.WATER
    # TOTAL (not TOTAL_INCREASING) paired with last_reset tells HA this value
    # resets daily.  TOTAL_INCREASING would treat the midnight reset (and any
    # mid-day decrease) as a counter roll-over, adding phantom consumption.
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfVolume.GALLONS
    _attr_icon = "mdi:water-pump"

    def __init__(self, coordinator):
        """Initialize the sensor."""
        super().__init__(coordinator)
        acct = entry_id_slug(coordinator.entry.entry_id)
        self.entity_id = f"sensor.sjwater_{acct}_todays_water_usage"
        self._attr_unique_id = f"sjwater_{acct}_todays_water_usage"
        self._last_reset_midnight: datetime | None = None

    @property
    def device_info(self):
        """Return device information to link entities up."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.entry.entry_id)},
            "name": "SJ Water Hub",
            "manufacturer": "SJ Water Company",
            "model": "Scraped Hub",
        }

    @property
    def native_value(self):
        """Return just the water used today."""
        if self.coordinator.data is not None:
            return max(0.0, self.coordinator.data.get("today_sum", 0.0))
        return None

    @property
    def extra_state_attributes(self):
        """Include reading and successful-update timestamps."""
        if self.coordinator.data is not None:
            return {
                "recorded_at": _local_timestamp(self.coordinator.data.get("timestamp")),
                "data_updated_at": _local_timestamp(
                    self.coordinator.data.get("data_updated_at")
                ),
                "last_successful_update": _local_timestamp(
                    self.coordinator.data.get("last_successful_update")
                ),
            }
        return {}

    @property
    def last_reset(self):
        """Return the start of today so HA knows this counter resets daily."""
        now = dt_util.now()
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self._last_reset_midnight is None or self._last_reset_midnight.date() != today_midnight.date():
            self._last_reset_midnight = today_midnight
        return self._last_reset_midnight
