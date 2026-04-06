"""Switch platform for Lumin Smart Panel - circuit relay control."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
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
    """Set up Lumin circuit switches.

    Only creates switches for circuits where controllable=True and main=False.
    Main circuits (e.g. "Everything Else") are metering-only and cannot be toggled.
    """
    coordinator: LuminDataCoordinator = entry.runtime_data
    entities: list[LuminCircuitSwitch] = []

    for guid, panel in coordinator.client.panels.items():
        circuits = coordinator.get_circuits(guid)
        for circuit in circuits:
            if circuit.get("controllable", False) and not circuit.get("main", False):
                entities.append(
                    LuminCircuitSwitch(coordinator, panel, circuit)
                )

    async_add_entities(entities)


class LuminCircuitSwitch(CoordinatorEntity[LuminDataCoordinator], SwitchEntity):
    """A switch entity representing a Lumin circuit relay."""

    _attr_device_class = SwitchDeviceClass.SWITCH
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
        self._attr_unique_id = f"lumin_{panel.guid}_{self._circuit_id}_switch"
        self._attr_name = circuit.get("name", f"Circuit {self._circuit_num}")
        self._attr_is_on = circuit.get("power_on", False)

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
            self._attr_is_on = circuit.get("power_on", False)
            self._attr_name = circuit.get("name", f"Circuit {self._circuit_num}")
            self._attr_extra_state_attributes = {
                "circuit_id": self._circuit_id,
                "circuit_num": circuit.get("num"),
                "locked": circuit.get("locked", False),
                "protected": circuit.get("protected", False),
                "active": circuit.get("active", False),
                "manual_on": circuit.get("manual_on", False),
                "max_power_w": circuit.get("max_power", 0),
                "spm_control_pref": circuit.get("spm_control_pref", ""),
                "phase": circuit.get("phase"),
                "lsp_id": circuit.get("lsp_id"),
            }
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self.coordinator.is_panel_available(self._panel.guid)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.switch_circuit(
            self._panel, self._circuit_id, True
        )
        self._attr_is_on = True
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.switch_circuit(
            self._panel, self._circuit_id, False
        )
        self._attr_is_on = False
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
