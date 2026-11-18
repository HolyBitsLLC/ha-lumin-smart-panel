"""Data coordinator for Lumin Smart Panel."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    LuminApiClient,
    LuminAuthError,
    LuminConnectionError,
    LuminReauthRequired,
)
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
        # Pairing, taken from the panel's own circuit metadata (the `pair`
        # object on each circuit). Never configured in Home Assistant: a pair
        # created or removed in the Lumin app is picked up by the next poll.
        # {primary_circuit_id: paired_leg_circuit_id}
        self._pair_of: dict[str, str] = {}
        # Ids that are only ever the second leg of a pair.
        self._pair_legs: set[str] = set()

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

    def _rebuild_pair_map(self, circuits: list[dict[str, Any]]) -> None:
        """Rebuild the circuit -> paired-leg map from a circuits payload.

        The panel is the only source of pairing. A two-pole load is reported as
        one leg per circuit, and the `pair` object names the leg that belongs to
        the same physical load, so the two halves are reunited here rather than
        by asking the user to configure anything in Home Assistant.
        """
        pair_of: dict[str, str] = {}
        for circuit in circuits:
            circuit_id = circuit.get("id")
            pair = circuit.get("pair")
            pair_id = pair.get("id") if isinstance(pair, dict) else None
            if circuit_id is None or pair_id is None:
                continue
            primary, leg = str(circuit_id), str(pair_id)
            if primary == leg:
                # A circuit is never its own pair.
                continue
            pair_of[primary] = leg

        # A well-formed payload carries one directed edge per pair. If a panel
        # ever reported both directions, the two circuits would each sum the
        # other's reading and the load would be counted twice; keep the lower id
        # as the primary so every physical load is attributed exactly once.
        for primary in list(pair_of):
            leg = pair_of[primary]
            if pair_of.get(leg) == primary and primary > leg:
                del pair_of[primary]

        self._pair_of = pair_of
        self._pair_legs = set(pair_of.values()) - set(pair_of)

    def is_pair_leg(self, circuit_id: int) -> bool:
        """True when this id is only the second leg of a paired load."""
        return str(circuit_id) in self._pair_legs

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

    def _own_power(self, circuit_id: int | str) -> float | None:
        """Latest raw reading for one leg, without its paired leg."""
        return self._live_power.get(str(circuit_id))

    def get_live_power(self, circuit_id: int) -> float | None:
        """Latest live power for a circuit, summing its paired leg.

        A two-pole load reports one leg per circuit, and the panel's `pair`
        object reunites them. This is the same quantity Lumin's own per-circuit
        display shows. The `multiplier` field is deliberately NOT applied here:
        it scales the "everything else" aggregate, not an individual circuit.
        """
        own = self._own_power(circuit_id)
        if own is None:
            return None
        pair_id = self._pair_of.get(str(circuit_id))
        if pair_id is not None:
            leg = self._live_power.get(pair_id)
            if leg is not None:
                own += leg
        return own

    def get_panel_power(self, guid: str) -> float | None:
        """Whole-panel power, using Lumin's own precedence.

        When the panel has a main circuit, the total is that circuit plus its
        paired leg — the meter already covers everything downstream, so adding
        the individual circuits would double-count. Only a panel without a main
        circuit falls back to summing each logical circuit once (primary plus
        its pair, never the pair on its own).
        """
        circuits = self.get_circuits(guid)
        if not circuits:
            return None

        mains = [circuit for circuit in circuits if circuit.get("main")]
        if mains:
            total = 0.0
            for circuit in mains:
                total += self._own_power(circuit["id"]) or 0.0
                pair_id = self._pair_of.get(str(circuit["id"]))
                if pair_id is not None:
                    total += self._live_power.get(pair_id) or 0.0
            return total

        total = 0.0
        for circuit in circuits:
            if self.is_pair_leg(circuit["id"]):
                continue
            power = self.get_live_power(circuit["id"])
            if power is not None:
                total += power
        return total

    def get_everything_else(self, guid: str) -> float:
        """Lumin's "Everything Else" figure: the unmonitored remainder.

        Mirrors the panel's own aggregate: a visible circuit with a multiplier
        contributes `multiplier * power`, and its paired leg contributes
        `multiplier * leg`. The multiplier carries the sign that subtracts
        monitored load from the main meter, which is why it belongs only to this
        aggregate and never to a circuit's own reading.
        """
        total = 0.0
        for circuit in self.get_circuits(guid):
            if circuit.get("hidden"):
                continue
            multiplier = circuit.get("multiplier")
            if not multiplier:
                continue
            total += multiplier * (self._own_power(circuit["id"]) or 0.0)
            pair_id = self._pair_of.get(str(circuit["id"]))
            if pair_id is not None:
                total += multiplier * (self._live_power.get(pair_id) or 0.0)
        return total

    def get_circuit_power(self, guid: str, circuit_id: int) -> float | None:
        """Power to display for one circuit tile.

        A main (metering-only) circuit shows the same "Everything Else"
        aggregate the Lumin app shows for it; every other circuit shows its own
        reading plus its paired leg.
        """
        circuit = self.get_circuit(guid, circuit_id)
        if circuit is not None and circuit.get("main"):
            # Nothing has streamed yet: report unknown rather than a
            # confident 0 W from an empty aggregate.
            if not self._live_power:
                return None
            return self.get_everything_else(guid)
        return self.get_live_power(circuit_id)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch circuit data from all panels."""
        data: dict[str, Any] = {"panels": {}}
        # Pairing is rebuilt from every card of circuits this poll returned. It
        # is accumulated across panels and only replaced when at least one panel
        # answered, so a single failed or empty poll never silently un-pairs.
        pair_circuits: list[dict[str, Any]] = []

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
                pair_circuits.extend(circuits)
            except LuminReauthRequired as err:
                # The refresh token is dead. Stop polling a grant that cannot
                # succeed and let Home Assistant start a reauth flow.
                raise ConfigEntryAuthFailed(
                    "The Lumin refresh token is no longer valid; "
                    "fresh token data is required"
                ) from err
            except (LuminConnectionError, LuminAuthError) as err:
                panel.available = False
                _LOGGER.debug("Update failed for panel %s: %s", guid, err)
                # Return stale data rather than failing entirely
                if panel.circuits:
                    panel_data["circuits"] = panel.circuits
                    pair_circuits.extend(panel.circuits)

            data["panels"][guid] = panel_data

        if not any(p.get("available") or p.get("circuits") for p in data["panels"].values()):
            raise UpdateFailed("All panels unreachable and no cached data")

        if pair_circuits:
            self._rebuild_pair_map(pair_circuits)

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
