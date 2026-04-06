"""WebSocket client for real-time power readings from Lumin Smart Panels.

Protocol (decoded from portal JS bundle):
- Cloud: wss://ws.luminsmart.com:50055/ws?Authorization=Bearer%20{token}
- Local: ws://{ip}:8085/ws (no auth required)
- Subscribe: send {"type": 0, "payload": {"lsp_id": <device_id>}}
  - For local connections, lsp_id can be 0 or the actual device ID.
- Server pushes {"type": <int>, "event": <data>}
  - Type 6: Power readings — {"readings": {"circuit_id": {"power": W}}, "time": ts}
  - Type 7: Circuit active state change — {"circuit_id": id, "turned": bool}
  - Type 4: Circuit switched — {"circuit_id": id, "power_on": bool, "reason": int}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import aiohttp

from .const import CLOUD_WS_URL, LOCAL_WS_PORT

_LOGGER = logging.getLogger(__name__)

# WebSocket message types (server → client)
WS_TYPE_SUBSCRIBE = 0
WS_TYPE_POWER_READINGS = 6
WS_TYPE_CIRCUIT_ACTIVE = 7
WS_TYPE_CIRCUIT_SWITCHED = 4

# Reconnect backoff
RECONNECT_MIN_DELAY = 2
RECONNECT_MAX_DELAY = 60


class LuminWebSocketClient:
    """Manages a WebSocket connection to a Lumin panel for live power data.

    Prefers local WS (no auth, lower latency). Falls back to cloud WS if
    no local IP is configured. Automatically reconnects on disconnect."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        panel_ip: str | None,
        lsp_id: int,
        access_token: str,
        on_power_readings: Callable[[dict[str, float]], None],
        on_circuit_event: Callable[[int, str, Any], None] | None = None,
    ) -> None:
        self._session = session
        self._panel_ip = panel_ip
        self._lsp_id = lsp_id
        self._token = access_token
        self._on_power_readings = on_power_readings
        self._on_circuit_event = on_circuit_event
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._reconnect_delay = RECONNECT_MIN_DELAY

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    def _local_url(self) -> str | None:
        if not self._panel_ip:
            return None
        return f"ws://{self._panel_ip}:{LOCAL_WS_PORT}/ws"

    def _cloud_url(self) -> str:
        return f"{CLOUD_WS_URL}?Authorization=Bearer%20{self._token}"

    def update_token(self, token: str) -> None:
        self._token = token

    async def start(self) -> None:
        """Start the WebSocket listener loop."""
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the WebSocket listener."""
        self._stop_event.set()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        """Reconnecting event loop."""
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.debug("WS connection ended: %s", err)

            if self._stop_event.is_set():
                break

            _LOGGER.debug(
                "Reconnecting in %ds (lsp_id=%d)",
                self._reconnect_delay,
                self._lsp_id,
            )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._reconnect_delay
                )
                break  # stop_event was set during wait
            except asyncio.TimeoutError:
                pass

            self._reconnect_delay = min(
                self._reconnect_delay * 2, RECONNECT_MAX_DELAY
            )

    async def _connect_and_listen(self) -> None:
        """Connect to WS and process messages until disconnection."""
        local_url = self._local_url()
        cloud_url = self._cloud_url()

        # Try local first
        url = local_url or cloud_url
        source = "local" if local_url else "cloud"

        try:
            self._ws = await self._session.ws_connect(
                url, heartbeat=20, timeout=10
            )
        except Exception as err:
            if local_url:
                _LOGGER.debug("Local WS failed (%s), trying cloud", err)
                url = cloud_url
                source = "cloud"
                self._ws = await self._session.ws_connect(
                    url, heartbeat=20, timeout=10
                )
            else:
                raise

        _LOGGER.debug("WS connected (%s) for lsp_id=%d", source, self._lsp_id)
        self._reconnect_delay = RECONNECT_MIN_DELAY

        # Subscribe — use actual lsp_id for both local and cloud
        await self._ws.send_json(
            {"type": WS_TYPE_SUBSCRIBE, "payload": {"lsp_id": self._lsp_id}}
        )

        async for msg in self._ws:
            if self._stop_event.is_set():
                break

            if msg.type == aiohttp.WSMsgType.TEXT:
                self._handle_message(msg.data)
            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
                aiohttp.WSMsgType.CLOSING,
            ):
                _LOGGER.debug("WS %s for lsp_id=%d", msg.type.name, self._lsp_id)
                break

    def _handle_message(self, raw: str) -> None:
        """Parse and dispatch a WS message."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        msg_type = data.get("type")
        event = data.get("event")
        if event is None:
            return

        if msg_type == WS_TYPE_POWER_READINGS:
            readings = event.get("readings", {})
            power_map: dict[str, float] = {}
            for circuit_id_str, rdata in readings.items():
                if isinstance(rdata, dict):
                    power_map[circuit_id_str] = rdata.get("power", 0.0)
                else:
                    power_map[circuit_id_str] = float(rdata)
            if power_map:
                self._on_power_readings(power_map)

        elif msg_type == WS_TYPE_CIRCUIT_ACTIVE and self._on_circuit_event:
            self._on_circuit_event(
                event.get("circuit_id"), "active", event.get("turned")
            )

        elif msg_type == WS_TYPE_CIRCUIT_SWITCHED and self._on_circuit_event:
            self._on_circuit_event(
                event.get("circuit_id"),
                "switched",
                event.get("power_on"),
            )
