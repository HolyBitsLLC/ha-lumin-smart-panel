"""Sensor platform for Lumin Smart Panel.

Provides two sensor types per circuit:
  - Live Power (W): Real-time wattage from WebSocket stream.
  - Peak Power (W): Historical max power from REST API.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
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

            # Max-power sensor for circuits that have monitoring
            if circuit.get("max_power", 0) > 0 or circuit.get("spm_monitor"):
                entities.append(
                    LuminCircuitMaxPowerSensor(coordinator, panel, circuit)
                )

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
