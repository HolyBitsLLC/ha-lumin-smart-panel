"""Data coordinator for Lumin Smart Panel."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LuminApiClient, LuminConnectionError, LuminAuthError, LuminPanel
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .websocket import LuminWebSocketClient

_LOGGER = logging.getLogger(__name__)


class LuminDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage data fetching from Lumin panels.

    Fetches circuit data (including relay state, active status, max_power)
    for each panel via REST polling. Additionally maintains WebSocket
    connections for real-time power readings per circuit.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: LuminApiClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        # Live power readings: {circuit_id_str: watts}
        self._live_power: dict[str, float] = {}
        self._ws_clients: list[LuminWebSocketClient] = []

    async def async_setup_websockets(self) -> None:
        """Start WebSocket connections for all panels."""
        session = self.client._session
        token = self.client._token

        for guid, panel in self.client.panels.items():
            if not panel.lsp_id:
                continue
            ws = LuminWebSocketClient(
                session=session,
                panel_ip=panel.ip_address or None,
                lsp_id=panel.lsp_id,
                access_token=token,
                on_power_readings=self._handle_power_readings,
                on_circuit_event=self._handle_circuit_event,
            )
            self._ws_clients.append(ws)
            await ws.start()
            _LOGGER.debug("Started WS for panel %s (lsp_id=%d)", guid, panel.lsp_id)

    def update_ws_tokens(self, new_token: str) -> None:
        """Push a refreshed access token to all WebSocket clients."""
        for ws in self._ws_clients:
            ws.update_token(new_token)

    async def async_shutdown_websockets(self) -> None:
        """Stop all WebSocket connections."""
        for ws in self._ws_clients:
            await ws.stop()
        self._ws_clients.clear()

    @callback
    def _handle_power_readings(self, readings: dict[str, float]) -> None:
        """Called when a WS power reading arrives."""
        self._live_power.update(readings)
        self.async_set_updated_data(self.data)

    @callback
    def _handle_circuit_event(
        self, circuit_id: int, event_type: str, value: Any
    ) -> None:
        """Called when a WS circuit event (switch/active) arrives."""
        # Trigger a coordinator update so entities refresh
        self.async_set_updated_data(self.data)

    def get_live_power(self, circuit_id: int) -> float | None:
        """Get the latest live power reading for a circuit, or None."""
        return self._live_power.get(str(circuit_id))

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch circuit data from all panels."""
        data: dict[str, Any] = {"panels": {}}

        for guid, panel in self.client.panels.items():
            panel_data: dict[str, Any] = {
                "circuits": [],
                "available": False,
                "online": panel.online,
            }

            try:
                circuits = await self.client.get_circuits(panel)
                panel_data["circuits"] = circuits
                panel_data["available"] = True
                panel.circuits = circuits
                panel.available = True
            except (LuminConnectionError, LuminAuthError) as err:
                panel.available = False
                _LOGGER.debug("Update failed for panel %s: %s", guid, err)
                # Return stale data rather than failing entirely
                if panel.circuits:
                    panel_data["circuits"] = panel.circuits

            data["panels"][guid] = panel_data

        if not any(p.get("available") or p.get("circuits") for p in data["panels"].values()):
            raise UpdateFailed("All panels unreachable and no cached data")

        return data

    def get_circuit(self, guid: str, circuit_id: int) -> dict[str, Any] | None:
        """Get a specific circuit from cached data."""
        if not self.data:
            return None
        panel_data = self.data.get("panels", {}).get(guid, {})
        for circuit in panel_data.get("circuits", []):
            if circuit.get("id") == circuit_id:
                return circuit
        return None

    def get_circuits(self, guid: str) -> list[dict[str, Any]]:
        """Get all circuits for a panel."""
        if not self.data:
            return []
        return self.data.get("panels", {}).get(guid, {}).get("circuits", [])

    def is_panel_available(self, guid: str) -> bool:
        """Check if a panel is available (local or cloud reached)."""
        if not self.data:
            return False
        return self.data.get("panels", {}).get(guid, {}).get("available", False)
