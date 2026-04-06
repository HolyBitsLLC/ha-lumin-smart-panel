"""Binary sensor platform for Lumin Smart Panel."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
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
    """Set up Lumin binary sensors."""
    coordinator: LuminDataCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []

    for guid, panel in coordinator.client.panels.items():
        entities.append(LuminPanelConnectivity(coordinator, panel))

        # Per-circuit "active" binary sensor (is this circuit drawing power?)
        circuits = coordinator.get_circuits(guid)
        for circuit in circuits:
            if not circuit.get("main", False):
                entities.append(
                    LuminCircuitActive(coordinator, panel, circuit)
                )

    async_add_entities(entities)


class LuminPanelConnectivity(
    CoordinatorEntity[LuminDataCoordinator], BinarySensorEntity
):
    """Binary sensor indicating panel reachability (local or cloud)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True

    def __init__(self, coordinator: LuminDataCoordinator, panel: Any) -> None:
        super().__init__(coordinator)
        self._panel = panel
        self._attr_unique_id = f"lumin_{panel.guid}_connectivity"
        self._attr_name = "Panel Connected"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._panel.guid)},
            name=self._panel.name,
            manufacturer="Lumin",
            model="Smart Panel",
            sw_version=self._panel.firmware_version,
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_panel_available(self._panel.guid)


class LuminCircuitActive(
    CoordinatorEntity[LuminDataCoordinator], BinarySensorEntity
):
    """Binary sensor: is this circuit actively drawing power?

    The 'active' field in the circuit data is a boolean set by the panel
    when it detects current flow through the circuit's CT sensor.
    """

    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_has_entity_name = True

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
        self._attr_unique_id = f"lumin_{panel.guid}_{self._circuit_id}_active"
        self._attr_name = f"{circuit_name} Active"
        self._attr_is_on = circuit.get("active", False)

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
            self._attr_is_on = circuit.get("active", False)
            circuit_name = circuit.get("name", f"Circuit {self._circuit_num}")
            self._attr_name = f"{circuit_name} Active"
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        circuit = self.coordinator.get_circuit(self._panel.guid, self._circuit_id)
        if not circuit:
            return None
        return circuit.get("active", False)

    @property
    def available(self) -> bool:
        return self.coordinator.is_panel_available(self._panel.guid)
