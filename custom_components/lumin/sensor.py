"""Sensor platform for Lumin Smart Panel.

Provides three sensor types per circuit:
  - Live Power (W): Real-time wattage from WebSocket stream.
  - Energy (kWh): Accumulated energy via trapezoidal integration of power.
  - Peak Power (W): Historical max power from REST API.

Plus panel-level aggregate sensors:
  - Total Power (W): Sum of all circuit live power readings.
  - Total Energy (kWh): Sum of all circuit energy accumulators.

The Energy sensors use state_class TOTAL_INCREASING so they appear in
Home Assistant's Energy dashboard for grid consumption and per-device tracking.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LuminDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lumin sensors."""
    coordinator: LuminDataCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []

    for guid, panel in coordinator.client.panels.items():
        circuits = coordinator.get_circuits(guid)

        for circuit in circuits:
            # Live power sensor for every circuit (fed by WebSocket)
            entities.append(
                LuminCircuitPowerSensor(coordinator, panel, circuit)
            )

            # Energy sensor — integrates power to kWh (for Energy dashboard)
            entities.append(
                LuminCircuitEnergySensor(coordinator, panel, circuit)
            )

            # Max-power sensor for circuits that have monitoring
            if circuit.get("max_power", 0) > 0 or circuit.get("spm_monitor"):
                entities.append(
                    LuminCircuitMaxPowerSensor(coordinator, panel, circuit)
                )

        # Panel-level aggregate sensors
        entities.append(LuminPanelTotalPowerSensor(coordinator, panel))
        entities.append(LuminPanelTotalEnergySensor(coordinator, panel))

    async_add_entities(entities)


class LuminCircuitPowerSensor(
    CoordinatorEntity[LuminDataCoordinator], SensorEntity
):
    """Real-time power draw for a circuit, updated via WebSocket."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_has_entity_name = True
    _attr_icon = "mdi:flash"
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: LuminDataCoordinator,
        panel: Any,
        circuit: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._panel = panel
        self._circuit_id: int = circuit["id"]
        self._circuit_num: int = circuit.get("num", 0)
        circuit_name = circuit.get("name", f"Circuit {self._circuit_num}")
        self._attr_unique_id = f"lumin_{panel.guid}_{self._circuit_id}_power"
        self._attr_name = f"{circuit_name} Power"
        self._attr_native_value = coordinator.get_live_power(self._circuit_id)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._panel.guid)},
            name=self._panel.name,
            manufacturer="Lumin",
            model="Smart Panel",
            sw_version=self._panel.firmware_version,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        power = self.coordinator.get_live_power(self._circuit_id)
        if power is not None:
            self._attr_native_value = round(power, 1)
        circuit = self.coordinator.get_circuit(self._panel.guid, self._circuit_id)
        if circuit:
            circuit_name = circuit.get("name", f"Circuit {self._circuit_num}")
            self._attr_name = f"{circuit_name} Power"
            self._attr_extra_state_attributes = {
                "circuit_id": self._circuit_id,
                "circuit_num": circuit.get("num"),
                "power_on": circuit.get("power_on", False),
                "active": circuit.get("active", False),
                "main": circuit.get("main", False),
            }
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.coordinator.is_panel_available(self._panel.guid)


class LuminCircuitMaxPowerSensor(
    CoordinatorEntity[LuminDataCoordinator], SensorEntity
):
    """Peak (historical max) power sensor for an individual circuit.

    This is the highest wattage the Lumin panel has ever observed on this
    circuit.  It updates when a new peak is recorded, which is useful for
    sizing and capacity planning.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_has_entity_name = True
    _attr_icon = "mdi:flash-triangle"

    def __init__(
        self,
        coordinator: LuminDataCoordinator,
        panel: Any,
        circuit: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._panel = panel
        self._circuit_id: int = circuit["id"]
        self._circuit_num: int = circuit.get("num", 0)
        circuit_name = circuit.get("name", f"Circuit {self._circuit_num}")
        self._attr_unique_id = f"lumin_{panel.guid}_{self._circuit_id}_max_power"
        self._attr_name = f"{circuit_name} Peak Power"
        self._attr_native_value = circuit.get("max_power", 0)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._panel.guid)},
            name=self._panel.name,
            manufacturer="Lumin",
            model="Smart Panel",
            sw_version=self._panel.firmware_version,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        circuit = self.coordinator.get_circuit(self._panel.guid, self._circuit_id)
        if circuit:
            self._attr_native_value = circuit.get("max_power", 0)
            circuit_name = circuit.get("name", f"Circuit {self._circuit_num}")
            self._attr_name = f"{circuit_name} Peak Power"
            self._attr_extra_state_attributes = {
                "circuit_id": self._circuit_id,
                "circuit_num": circuit.get("num"),
                "active": circuit.get("active", False),
                "power_on": circuit.get("power_on", False),
                "main": circuit.get("main", False),
                "spm_control_pref": circuit.get("spm_control_pref", ""),
            }
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.coordinator.is_panel_available(self._panel.guid)


class LuminCircuitEnergySensor(
    CoordinatorEntity[LuminDataCoordinator], RestoreEntity, SensorEntity
):
    """Accumulated energy consumption for a circuit (kWh).

    Integrates instantaneous power readings over time using trapezoidal
    integration. Persists total across HA restarts via RestoreEntity.

    Eligible for the Energy dashboard as an individual device sensor
    (state_class: TOTAL_INCREASING, device_class: ENERGY).
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_has_entity_name = True
    _attr_icon = "mdi:lightning-bolt"
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: LuminDataCoordinator,
        panel: Any,
        circuit: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._panel = panel
        self._circuit_id: int = circuit["id"]
        self._circuit_num: int = circuit.get("num", 0)
        circuit_name = circuit.get("name", f"Circuit {self._circuit_num}")
        self._attr_unique_id = f"lumin_{panel.guid}_{self._circuit_id}_energy"
        self._attr_name = f"{circuit_name} Energy"
        self._total_energy: float = 0.0
        self._last_power: float | None = None
        self._last_update: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore previous energy total on startup."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._total_energy = float(last_state.state)
            except (ValueError, TypeError):
                self._total_energy = 0.0
        self._attr_native_value = round(self._total_energy, 3)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._panel.guid)},
            name=self._panel.name,
            manufacturer="Lumin",
            model="Smart Panel",
            sw_version=self._panel.firmware_version,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        power = self.coordinator.get_live_power(self._circuit_id)
        if power is not None and power >= 0:
            now = time.monotonic()
            if self._last_power is not None and self._last_update is not None:
                dt_hours = (now - self._last_update) / 3600.0
                # Trapezoidal integration: average of last and current reading
                avg_power = (self._last_power + power) / 2.0
                energy_kwh = avg_power * dt_hours / 1000.0
                if energy_kwh > 0:
                    self._total_energy += energy_kwh
            self._last_power = power
            self._last_update = now
            self._attr_native_value = round(self._total_energy, 3)
        # Update name in case circuit was renamed
        circuit = self.coordinator.get_circuit(
            self._panel.guid, self._circuit_id
        )
        if circuit:
            circuit_name = circuit.get("name", f"Circuit {self._circuit_num}")
            self._attr_name = f"{circuit_name} Energy"
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.coordinator.is_panel_available(self._panel.guid)


class LuminPanelTotalPowerSensor(
    CoordinatorEntity[LuminDataCoordinator], SensorEntity
):
    """Total instantaneous power across all circuits in a panel (W).

    Useful as a real-time whole-home power gauge.
    """

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_has_entity_name = True
    _attr_icon = "mdi:home-lightning-bolt"
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: LuminDataCoordinator,
        panel: Any,
    ) -> None:
        super().__init__(coordinator)
        self._panel = panel
        self._attr_unique_id = f"lumin_{panel.guid}_total_power"
        self._attr_name = f"{panel.name} Total Power"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._panel.guid)},
            name=self._panel.name,
            manufacturer="Lumin",
            model="Smart Panel",
            sw_version=self._panel.firmware_version,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        total = 0.0
        circuits = self.coordinator.get_circuits(self._panel.guid)
        for circuit in circuits:
            power = self.coordinator.get_live_power(circuit["id"])
            if power is not None and power >= 0:
                total += power
        self._attr_native_value = round(total, 0)
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.coordinator.is_panel_available(self._panel.guid)


class LuminPanelTotalEnergySensor(
    CoordinatorEntity[LuminDataCoordinator], RestoreEntity, SensorEntity
):
    """Total accumulated energy across all circuits in a panel (kWh).

    This is the sensor to use for the Energy dashboard's "Grid consumption"
    if the Lumin panel covers the whole home's electrical panel.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_has_entity_name = True
    _attr_icon = "mdi:home-lightning-bolt"
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: LuminDataCoordinator,
        panel: Any,
    ) -> None:
        super().__init__(coordinator)
        self._panel = panel
        self._attr_unique_id = f"lumin_{panel.guid}_total_energy"
        self._attr_name = f"{panel.name} Total Energy"
        self._total_energy: float = 0.0
        self._last_total_power: float | None = None
        self._last_update: float | None = None

    async def async_added_to_hass(self) -> None:
        """Restore previous energy total on startup."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._total_energy = float(last_state.state)
            except (ValueError, TypeError):
                self._total_energy = 0.0
        self._attr_native_value = round(self._total_energy, 3)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._panel.guid)},
            name=self._panel.name,
            manufacturer="Lumin",
            model="Smart Panel",
            sw_version=self._panel.firmware_version,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        total_power = 0.0
        circuits = self.coordinator.get_circuits(self._panel.guid)
        for circuit in circuits:
            power = self.coordinator.get_live_power(circuit["id"])
            if power is not None and power >= 0:
                total_power += power

        now = time.monotonic()
        if (
            self._last_total_power is not None
            and self._last_update is not None
        ):
            dt_hours = (now - self._last_update) / 3600.0
            avg_power = (self._last_total_power + total_power) / 2.0
            energy_kwh = avg_power * dt_hours / 1000.0
            if energy_kwh > 0:
                self._total_energy += energy_kwh

        self._last_total_power = total_power
        self._last_update = now
        self._attr_native_value = round(self._total_energy, 3)
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.coordinator.is_panel_available(self._panel.guid)
