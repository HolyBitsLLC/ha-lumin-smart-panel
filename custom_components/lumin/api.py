"""API client for Lumin Smart Panel - local-first with cloud fallback.

Local API (panel port 443, self-signed TLS):
  GET  /v2/lsps                              → list panels (JSON)
  GET  /v2/lsps/{id}                         → single panel detail (JSON)
  GET  /v2/lsps/{id}/circuits                → list circuits (JSON)
  GET  /v2/lsps/{id}/circuits/{cid}          → single circuit (JSON)
  PUT  /v2/lsps/{id}/circuits/{cid}/switch?v=true|false → toggle relay (JSON)
  PUT  /v2/lsps/{id}/circuits/{cid}          → update circuit (JSON)
  All other paths return setup-wizard HTML.

Cloud API (api.luminsmart.com):
  Same /v2/lsps/* endpoints plus:
  GET  /v2/lsps/{id}/status                  → full panel status (JSON)
  GET  /v2/locations                          → account locations (JSON)
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import ssl

_LOGGER = logging.getLogger(__name__)


class LuminAuthError(Exception):
    """Authentication error."""


class LuminConnectionError(Exception):
    """Connection error."""


class LuminPanel:
    """Represents a single Lumin Smart Panel device."""

    def __init__(
        self,
        ip_address: str,
        guid: str,
        name: str | None = None,
        lsp_id: int | None = None,
    ) -> None:
        self.ip_address = ip_address
        self.guid = guid
        self.name = name or f"Lumin {guid[-6:]}"
        self.lsp_id = lsp_id
        self.firmware_version: str | None = None
        self.online: bool = False
        self.grid_detector: bool = False
        self.circuits: list[dict[str, Any]] = []
        self.available: bool = False


class LuminLocalClient:
    """Client for direct local communication with a Lumin panel on port 443.

    Only /v2/lsps* and /v2/lsps/{id}/circuits* paths return JSON.
    Everything else falls through to the setup-wizard HTML.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        panel: LuminPanel,
        access_token: str,
    ) -> None:
        self._session = session
        self._panel = panel
        self._token = access_token
        self._ssl_context = ssl.create_default_context()
        self._ssl_context.check_hostname = False
        self._ssl_context.verify_mode = ssl.CERT_NONE

    @property
    def base_url(self) -> str:
        return f"https://{self._panel.ip_address}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self, method: str, path: str, json_data: dict | None = None
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                headers=self._headers(),
                json=json_data,
                ssl=self._ssl_context,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 401:
                    raise LuminAuthError("Invalid or expired token")
                if resp.status == 405:
                    raise LuminConnectionError(
                        f"Method {method} not allowed on {path}"
                    )
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                if isinstance(data, dict) and data.get("success") is False:
                    error = data.get("error", {})
                    if error.get("code") == 401:
                        raise LuminAuthError(error.get("message", "Unauthorized"))
                    raise LuminConnectionError(
                        error.get("message", "API returned error")
                    )
                return data
        except (aiohttp.ContentTypeError, ValueError) as err:
            raise LuminConnectionError(
                f"Local API returned non-JSON (likely setup wizard HTML) for {path}"
            ) from err
        except aiohttp.ClientError as err:
            raise LuminConnectionError(
                f"Local connection to {self._panel.ip_address} failed: {err}"
            ) from err

    async def get_panels(self) -> list[dict[str, Any]]:
        """List panels visible from this device."""
        data = await self._request("GET", "/v2/lsps")
        return data.get("devices", [])

    async def get_panel(self, lsp_id: int) -> dict[str, Any]:
        """Get a single panel's detail."""
        data = await self._request("GET", f"/v2/lsps/{lsp_id}")
        return data.get("device", {})

    async def get_circuits(self) -> list[dict[str, Any]]:
        """Get all circuits for this panel."""
        lsp_id = self._panel.lsp_id
        if not lsp_id:
            # Discover lsp_id from device list
            panels = await self.get_panels()
            if panels:
                lsp_id = panels[0].get("id")
                self._panel.lsp_id = lsp_id
        if not lsp_id:
            return []
        data = await self._request("GET", f"/v2/lsps/{lsp_id}/circuits")
        return data.get("circuits", [])

    async def switch_circuit(self, circuit_id: int, turn_on: bool) -> dict[str, Any]:
        """Toggle a circuit relay on or off."""
        lsp_id = self._panel.lsp_id
        if not lsp_id:
            raise LuminConnectionError("Panel lsp_id not known")
        v = "true" if turn_on else "false"
        return await self._request(
            "PUT",
            f"/v2/lsps/{lsp_id}/circuits/{circuit_id}/switch?v={v}",
        )

    async def lock_circuit(self, circuit_id: int, locked: bool) -> dict[str, Any]:
        """Lock or unlock a circuit."""
        lsp_id = self._panel.lsp_id
        if not lsp_id:
            raise LuminConnectionError("Panel lsp_id not known")
        return await self._request(
            "PUT",
            f"/v2/lsps/{lsp_id}/circuits/{circuit_id}",
            json_data={"locked": locked},
        )

    async def test_connection(self) -> bool:
        """Test if we can connect to the panel locally."""
        try:
            panels = await self.get_panels()
            return len(panels) > 0
        except (LuminAuthError, LuminConnectionError):
            return False


class LuminCloudClient:
    """Client for Lumin cloud API (api.luminsmart.com)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        cloud_base: str = "https://api.luminsmart.com",
    ) -> None:
        self._session = session
        self._token = access_token
        self._base = cloud_base

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self, method: str, path: str, json_data: dict | None = None
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        try:
            async with self._session.request(
                method,
                url,
                headers=self._headers(),
                json=json_data,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 401:
                    raise LuminAuthError("Invalid or expired token")
                resp.raise_for_status()
                data = await resp.json()
                if isinstance(data, dict) and data.get("success") is False:
                    error = data.get("error", {})
                    if error.get("code") == 401:
                        raise LuminAuthError(error.get("message", "Unauthorized"))
                    raise LuminConnectionError(
                        error.get("message", "API returned error")
                    )
                return data
        except aiohttp.ClientError as err:
            raise LuminConnectionError(f"Cloud API request failed: {err}") from err

    async def get_panels(self) -> list[dict[str, Any]]:
        """Get all panels linked to the account."""
        data = await self._request("GET", "/v2/lsps")
        return data.get("devices", [])

    async def get_panel_status(self, lsp_id: int) -> dict[str, Any]:
        """Get full status for a panel (includes circuits, modes, sensorboards)."""
        data = await self._request("GET", f"/v2/lsps/{lsp_id}/status")
        return data.get("device", {})

    async def get_circuits(self, lsp_id: int) -> list[dict[str, Any]]:
        """Get circuits for a panel."""
        data = await self._request("GET", f"/v2/lsps/{lsp_id}/circuits")
        return data.get("circuits", [])

    async def switch_circuit(
        self, lsp_id: int, circuit_id: int, turn_on: bool
    ) -> dict[str, Any]:
        """Toggle a circuit."""
        v = "true" if turn_on else "false"
        return await self._request(
            "PUT",
            f"/v2/lsps/{lsp_id}/circuits/{circuit_id}/switch?v={v}",
        )

    async def lock_circuit(
        self, lsp_id: int, circuit_id: int, locked: bool
    ) -> dict[str, Any]:
        """Lock/unlock a circuit."""
        return await self._request(
            "PUT",
            f"/v2/lsps/{lsp_id}/circuits/{circuit_id}",
            json_data={"locked": locked},
        )

    async def get_locations(self) -> list[dict[str, Any]]:
        """Get account locations (each location groups panels)."""
        data = await self._request("GET", "/v2/locations")
        return data.get("locations", [])


class LuminApiClient:
    """Unified API client: local-first with cloud fallback."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        panels: list[LuminPanel],
        use_cloud_fallback: bool = True,
    ) -> None:
        self._session = session
        self._token = access_token
        self._panels = {p.guid: p for p in panels}
        self._use_cloud_fallback = use_cloud_fallback

        self._local_clients: dict[str, LuminLocalClient] = {}
        for panel in panels:
            if panel.ip_address:
                self._local_clients[panel.guid] = LuminLocalClient(
                    session, panel, access_token
                )

        self._cloud_client = LuminCloudClient(session, access_token)

    def update_token(self, access_token: str) -> None:
        """Update the access token for all clients."""
        self._token = access_token
        for client in self._local_clients.values():
            client._token = access_token
        self._cloud_client._token = access_token

    @property
    def panels(self) -> dict[str, LuminPanel]:
        return self._panels

    def get_local_client(self, guid: str) -> LuminLocalClient | None:
        return self._local_clients.get(guid)

    @property
    def cloud_client(self) -> LuminCloudClient:
        return self._cloud_client

    async def get_circuits(self, panel: LuminPanel) -> list[dict[str, Any]]:
        """Get circuits - try local first, fall back to cloud."""
        local = self._local_clients.get(panel.guid)
        if local:
            try:
                circuits = await local.get_circuits()
                panel.available = True
                return circuits
            except (LuminConnectionError, LuminAuthError) as err:
                _LOGGER.debug("Local API failed for %s: %s", panel.guid, err)
                panel.available = False

        if self._use_cloud_fallback and panel.lsp_id:
            try:
                circuits = await self._cloud_client.get_circuits(panel.lsp_id)
                return circuits
            except (LuminConnectionError, LuminAuthError) as err:
                _LOGGER.warning(
                    "Cloud fallback also failed for %s: %s", panel.guid, err
                )

        return []

    async def switch_circuit(
        self, panel: LuminPanel, circuit_id: int, turn_on: bool
    ) -> dict[str, Any] | None:
        """Switch circuit - try local first, fall back to cloud."""
        local = self._local_clients.get(panel.guid)
        if local:
            try:
                result = await local.switch_circuit(circuit_id, turn_on)
                panel.available = True
                return result
            except (LuminConnectionError, LuminAuthError) as err:
                _LOGGER.debug("Local switch failed for %s: %s", panel.guid, err)

        if self._use_cloud_fallback and panel.lsp_id:
            try:
                return await self._cloud_client.switch_circuit(
                    panel.lsp_id, circuit_id, turn_on
                )
            except (LuminConnectionError, LuminAuthError) as err:
                _LOGGER.error(
                    "Cloud switch also failed for %s: %s", panel.guid, err
                )

        return None

    async def get_panel_status(self, panel: LuminPanel) -> dict[str, Any]:
        """Get full panel status (cloud only — local /status returns HTML)."""
        if self._use_cloud_fallback and panel.lsp_id:
            try:
                return await self._cloud_client.get_panel_status(panel.lsp_id)
            except (LuminConnectionError, LuminAuthError) as err:
                _LOGGER.debug("Cloud status failed for %s: %s", panel.guid, err)
        return {}

    async def discover_panels_cloud(self) -> list[dict[str, Any]]:
        """Discover panels from the cloud account."""
        try:
            return await self._cloud_client.get_panels()
        except (LuminConnectionError, LuminAuthError) as err:
            _LOGGER.error("Failed to discover panels from cloud: %s", err)
            return []
