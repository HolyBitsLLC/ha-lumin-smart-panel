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
import time
from typing import Any, Callable, Coroutine

import aiohttp
import ssl

_LOGGER = logging.getLogger(__name__)

# Buffer before actual expiry to trigger proactive refresh (5 minutes)
TOKEN_EXPIRY_BUFFER = 300


class LuminAuthError(Exception):
    """Authentication error."""


class LuminConnectionError(Exception):
    """Connection error."""


class LuminTokenManager:
    """Manages Auth0 token lifecycle — refresh before expiry, retry on 401.

    Auth0 refresh endpoint: POST https://{domain}/oauth/token
    with grant_type=refresh_token, client_id, refresh_token.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        refresh_token: str,
        auth0_domain: str,
        client_id: str,
        on_tokens_refreshed: Callable[[str, str], Coroutine] | None = None,
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._auth0_domain = auth0_domain
        self._client_id = client_id
        self._on_tokens_refreshed = on_tokens_refreshed
        self._expires_at: float = 0  # unknown until first refresh
        self._refreshing = False

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    def set_expiry(self, expires_in: int) -> None:
        """Set token expiry from expires_in seconds."""
        self._expires_at = time.monotonic() + expires_in

    @property
    def token_expired(self) -> bool:
        """True if the token is expired or will expire within the buffer."""
        if self._expires_at == 0:
            return False  # unknown expiry, assume valid until 401
        return time.monotonic() >= (self._expires_at - TOKEN_EXPIRY_BUFFER)

    async def ensure_valid_token(self) -> str:
        """Return a valid access token, refreshing proactively if needed."""
        if self.token_expired and self._refresh_token:
            await self.async_refresh()
        return self._access_token

    async def async_refresh(self) -> None:
        """Exchange refresh token for a new access token via Auth0."""
        if self._refreshing:
            return
        if not self._refresh_token:
            raise LuminAuthError("No refresh token available")

        self._refreshing = True
        try:
            url = f"https://{self._auth0_domain}/oauth/token"
            payload = {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": self._refresh_token,
            }
            async with self._session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.error("Token refresh failed (%d): %s", resp.status, body)
                    raise LuminAuthError(
                        f"Token refresh failed with status {resp.status}"
                    )
                data = await resp.json()

            new_access = data.get("access_token")
            new_refresh = data.get("refresh_token", self._refresh_token)
            expires_in = data.get("expires_in", 86400)

            if not new_access:
                raise LuminAuthError("Token refresh returned no access_token")

            self._access_token = new_access
            self._refresh_token = new_refresh
            self._expires_at = time.monotonic() + expires_in
            _LOGGER.debug("Token refreshed, expires in %ds", expires_in)

            if self._on_tokens_refreshed:
                await self._on_tokens_refreshed(new_access, new_refresh)

        finally:
            self._refreshing = False


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
        token_manager: LuminTokenManager | None = None,
    ) -> None:
        self._session = session
        self._token = access_token
        self._panels = {p.guid: p for p in panels}
        self._use_cloud_fallback = use_cloud_fallback
        self._token_manager = token_manager

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

    async def async_ensure_token(self) -> None:
        """Proactively refresh the token if it's near expiry."""
        if self._token_manager:
            new_token = await self._token_manager.ensure_valid_token()
            if new_token != self._token:
                self.update_token(new_token)

    async def async_handle_auth_error(self) -> bool:
        """Attempt token refresh on 401. Returns True if refresh succeeded."""
        if not self._token_manager or not self._token_manager.refresh_token:
            return False
        try:
            await self._token_manager.async_refresh()
            self.update_token(self._token_manager.access_token)
            return True
        except LuminAuthError:
            return False

    @property
    def panels(self) -> dict[str, LuminPanel]:
        return self._panels

    def get_local_client(self, guid: str) -> LuminLocalClient | None:
        return self._local_clients.get(guid)

    @property
    def cloud_client(self) -> LuminCloudClient:
        return self._cloud_client

    async def get_circuits(self, panel: LuminPanel) -> list[dict[str, Any]]:
        """Get circuits - try local first, fall back to cloud. Retries on 401."""
        await self.async_ensure_token()

        local = self._local_clients.get(panel.guid)
        if local:
            try:
                circuits = await local.get_circuits()
                panel.available = True
                return circuits
            except LuminAuthError:
                if await self.async_handle_auth_error():
                    try:
                        circuits = await local.get_circuits()
                        panel.available = True
                        return circuits
                    except (LuminConnectionError, LuminAuthError) as err:
                        _LOGGER.debug("Local API retry failed for %s: %s", panel.guid, err)
                        panel.available = False
                else:
                    raise
            except LuminConnectionError as err:
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
        """Switch circuit - try local first, fall back to cloud. Retries on 401."""
        await self.async_ensure_token()

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
