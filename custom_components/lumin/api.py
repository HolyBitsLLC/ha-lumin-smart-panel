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

import asyncio
import base64
import json
import logging
import ssl
import time
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Buffer before actual expiry to trigger proactive refresh (5 minutes)
TOKEN_EXPIRY_BUFFER = 300

# Auth0 errors that mean the refresh token is *dead*, not merely that the call
# failed. Only a fresh grant from a human can recover from these.
REFRESH_TOKEN_DEAD_ERRORS = frozenset(
    {"invalid_grant", "invalid_client", "unauthorized_client"}
)


class LuminAuthError(Exception):
    """Authentication error."""


class LuminReauthRequired(LuminAuthError):
    """The stored grant can no longer be refreshed; a human must re-authenticate."""


class LuminConnectionError(Exception):
    """Connection error."""


def _jwt_payload(token: str) -> dict[str, Any] | None:
    """Decode a JWT payload, or None if the token is opaque/malformed.

    The signature is deliberately not verified: claims are only used to route
    a request (which client to refresh with) or to schedule a refresh, never
    to authorise one.
    """
    try:
        payload_segment = token.split(".")[1]
    except (AttributeError, IndexError):
        return None

    padded = payload_segment + "=" * (-len(payload_segment) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def jwt_claim(token: str, claim: str) -> Any | None:
    """Return an unverified JWT claim, or None."""
    payload = _jwt_payload(token)
    return payload.get(claim) if payload is not None else None


def jwt_expiry(token: str) -> float | None:
    """Return the `exp` claim of a JWT as epoch seconds, or None."""
    exp = jwt_claim(token, "exp")
    if isinstance(exp, (int, float)):
        return float(exp)
    return None


def client_id_candidates(
    access_token: str,
    known_client_ids: Sequence[str],
    stored_client_id: str | None = None,
) -> list[str]:
    """Order the candidate client ids for a refresh, best first.

    An Auth0 refresh token is bound to the client that was issued it, so
    refreshing with the wrong `client_id` fails with `invalid_grant`. The
    access token names its own issuer in the `azp` claim, which makes this
    exact for a pasted token; `stored_client_id` covers an entry that has
    already learned the answer, and the remaining known ids are fallbacks.
    """
    candidates: list[str] = []
    azp = jwt_claim(access_token, "azp")
    for candidate in (stored_client_id, azp, *known_client_ids):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _auth0_error_code(body: str) -> str:
    """Extract the OAuth `error` code from an Auth0 error body."""
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return ""
    if isinstance(parsed, dict):
        code = parsed.get("error")
        if isinstance(code, str):
            return code
    return ""


class _RefreshRejected(Exception):
    """Auth0 refused the refresh token for a specific client id."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class LuminTokenManager:
    """Manages the Auth0 token lifecycle for the Lumin cloud API.

    Auth0 refresh endpoint: POST https://{domain}/oauth/token with
    grant_type=refresh_token, client_id, refresh_token.

    The access token is short-lived; the refresh token is the durable
    credential. Expiry is kept as an absolute epoch timestamp (not a monotonic
    offset) so it can be stored in the config entry and still be meaningful
    after a Home Assistant restart, which is what lets a restart recover the
    session on its own.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        access_token: str,
        refresh_token: str,
        auth0_domain: str,
        client_id: str,
        on_tokens_refreshed: Callable[[str, str, float], Coroutine] | None = None,
        expires_at: float | None = None,
        client_id_fallbacks: Sequence[str] = (),
    ) -> None:
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._auth0_domain = auth0_domain
        # An Auth0 refresh token is bound to the client that issued it, so keep
        # every plausible id and try them in order rather than pinning one.
        self._client_ids: tuple[str, ...] = tuple(
            dict.fromkeys((client_id, *client_id_fallbacks))
        )
        self._client_id = self._client_ids[0]
        self._on_tokens_refreshed = on_tokens_refreshed
        # Prefer the persisted expiry; fall back to the token's own `exp` claim.
        # 0 means "unknown" — assume valid until a request says otherwise.
        self._expires_at: float = expires_at or jwt_expiry(access_token) or 0.0
        self._refresh_lock = asyncio.Lock()

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    @property
    def client_id(self) -> str:
        """The client id the grant is known to belong to."""
        return self._client_id

    @property
    def expires_at(self) -> float:
        """Absolute epoch expiry of the current access token (0 = unknown)."""
        return self._expires_at

    def set_expiry(self, expires_in: int) -> None:
        """Set absolute expiry from an `expires_in` seconds value."""
        if expires_in and expires_in > 0:
            self._expires_at = time.time() + expires_in

    @property
    def token_expired(self) -> bool:
        """True if the token is expired or expires within the buffer."""
        if self._expires_at == 0:
            return False  # unknown expiry, assume valid until a 401
        return time.time() >= (self._expires_at - TOKEN_EXPIRY_BUFFER)

    async def async_startup_refresh(self) -> None:
        """Make the stored grant usable before the first data poll.

        This is the path that recovers from a Home Assistant restart: the
        persisted access token is usually older than its lifetime, but the
        refresh token is not, so a single refresh restores the session with no
        human action.
        """
        if not self.token_expired:
            return

        if not self._refresh_token:
            raise LuminReauthRequired(
                "The stored access token has expired and no refresh token was saved"
            )

        await self.async_refresh()

    async def ensure_valid_token(self) -> str:
        """Return a valid access token, refreshing proactively if needed."""
        if self.token_expired and self._refresh_token:
            await self.async_refresh()
        return self._access_token

    async def async_refresh(self, stale_token: str | None = None) -> None:
        """Exchange the refresh token for a new access token via Auth0.

        Args:
            stale_token: the access token the caller found unusable. When it no
                longer matches the current token another task has already
                refreshed, so this call returns without burning a second
                refresh (Auth0 rotates refresh tokens and treats reuse as an
                attack).

        Raises:
            LuminReauthRequired: the refresh token is dead (reuse detected,
                revoked, or password changed). A human must re-authenticate.
            LuminAuthError: the refresh failed for a retryable reason.
            LuminConnectionError: the refresh could not reach Auth0.

        An Auth0 refresh token is bound to the client that was issued it, and
        the two Lumin clients (portal SPA and mobile app) issue different ones.
        Each candidate id from `client_id_candidates()` is tried once, so a
        token pasted from the portal still refreshes even though the mobile id
        was the previous default.
        """
        async with self._refresh_lock:
            if stale_token is not None and self._access_token != stale_token:
                return  # another task already replaced this token

            if not self._refresh_token:
                raise LuminReauthRequired("No refresh token available")

            data: dict[str, Any] | None = None
            rejected: tuple[str, int] = ("unknown", 0)
            for index, client_id in enumerate(self._client_ids):
                last_attempt = index == len(self._client_ids) - 1
                try:
                    data = await self._async_refresh_once(client_id, last_attempt)
                except _RefreshRejected as err:
                    rejected = (err.code, err.status)
                    continue
                self._client_id = client_id
                break

            if data is None:
                # Every candidate client was rejected, so the grant itself is
                # gone (revoked, rotated by another client, or password changed).
                code, _status = rejected
                raise LuminReauthRequired(
                    "The Lumin refresh token is no longer valid "
                    f"(Auth0: {code}); fresh token data is required"
                )

            new_access = data.get("access_token")
            new_refresh = data.get("refresh_token", self._refresh_token)
            expires_in = data.get("expires_in", 0)

            if not new_access:
                raise LuminAuthError("Token refresh returned no access_token")

            self._access_token = new_access
            self._refresh_token = new_refresh
            self.set_expiry(expires_in)
            if self._expires_at == 0:
                # No `expires_in` in the response; fall back to the JWT claim.
                self._expires_at = jwt_expiry(new_access) or 0.0
            _LOGGER.debug("Token refreshed, expires in %ss", expires_in)

            if self._on_tokens_refreshed:
                await self._on_tokens_refreshed(
                    new_access, new_refresh, self._expires_at
                )

    async def _async_refresh_once(
        self, client_id: str, log_failure: bool
    ) -> dict[str, Any]:
        """Try one refresh-token exchange with a specific client id.

        Raises `_RefreshRejected` when Auth0 rejects the token for this client
        (so the caller may try the next known client), `LuminAuthError` for a
        retryable failure, and `LuminConnectionError` when Auth0 is unreachable.
        """
        url = f"https://{self._auth0_domain}/oauth/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": self._refresh_token,
        }
        try:
            async with self._session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                status = resp.status
                body = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise LuminConnectionError(
                f"Could not reach the Auth0 token endpoint: {err}"
            ) from err

        if status != 200:
            code = _auth0_error_code(body)
            if log_failure:
                _LOGGER.error(
                    "Token refresh failed (%d, %s): %s",
                    status,
                    code or "no error code",
                    body[:200],
                )
            if code in REFRESH_TOKEN_DEAD_ERRORS:
                raise _RefreshRejected(code, status)
            raise LuminAuthError(f"Token refresh failed with status {status}")

        try:
            data = json.loads(body)
        except (ValueError, TypeError) as err:
            raise LuminAuthError("Token refresh returned a malformed body") from err
        if not isinstance(data, dict):
            raise LuminAuthError("Token refresh returned a malformed body")
        return data


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
        self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
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

    async def async_handle_auth_error(self, stale_token: str | None = None) -> bool:
        """Refresh after a 401. Returns True when a retry is worth attempting.

        Raises:
            LuminReauthRequired: there is no grant to refresh with, or the
                refresh token is dead, so Home Assistant has to ask the user
                for fresh token data.
        """
        if not self._token_manager:
            return False
        if not self._token_manager.refresh_token:
            if self._use_cloud_fallback:
                # An entry created from a bare access token has no refresh grant.
                # Returning False here would silently poll a 401 forever; raise
                # so the coordinator hands off to Home Assistant's reauth flow.
                raise LuminReauthRequired(
                    "The stored access token expired and no refresh token is "
                    "available; fresh token data is required"
                )
            return False
        try:
            await self._token_manager.async_refresh(
                stale_token if stale_token is not None else self._token
            )
        except LuminReauthRequired:
            raise
        except (LuminAuthError, LuminConnectionError) as err:
            _LOGGER.debug("Token refresh attempt failed: %s", err)
            return False
        self.update_token(self._token_manager.access_token)
        return True

    async def _call_with_reauth(self, call: Callable[[], Coroutine]) -> Any:
        """Run a request, and on a 401 refresh once and retry it."""
        stale_token = self._token
        try:
            return await call()
        except LuminAuthError:
            # Passing the token this request actually used lets a concurrent
            # refresh win without spending a second (rotating) refresh.
            if not await self.async_handle_auth_error(stale_token):
                raise
            return await call()

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
                circuits = await self._call_with_reauth(local.get_circuits)
                panel.available = True
                return circuits
            except LuminReauthRequired:
                raise
            except (LuminConnectionError, LuminAuthError) as err:
                _LOGGER.debug("Local API failed for %s: %s", panel.guid, err)
                panel.available = False

        if self._use_cloud_fallback and panel.lsp_id:
            try:
                return await self._call_with_reauth(
                    lambda: self._cloud_client.get_circuits(panel.lsp_id)
                )
            except LuminReauthRequired:
                raise
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
                result = await self._call_with_reauth(
                    lambda: local.switch_circuit(circuit_id, turn_on)
                )
                panel.available = True
                return result
            except LuminReauthRequired:
                raise
            except (LuminConnectionError, LuminAuthError) as err:
                _LOGGER.debug("Local switch failed for %s: %s", panel.guid, err)

        if self._use_cloud_fallback and panel.lsp_id:
            try:
                return await self._call_with_reauth(
                    lambda: self._cloud_client.switch_circuit(
                        panel.lsp_id, circuit_id, turn_on
                    )
                )
            except LuminReauthRequired:
                raise
            except (LuminConnectionError, LuminAuthError) as err:
                _LOGGER.error(
                    "Cloud switch also failed for %s: %s", panel.guid, err
                )

        return None

    async def get_panel_status(self, panel: LuminPanel) -> dict[str, Any]:
        """Get full panel status (cloud only — local /status returns HTML)."""
        if self._use_cloud_fallback and panel.lsp_id:
            try:
                return await self._call_with_reauth(
                    lambda: self._cloud_client.get_panel_status(panel.lsp_id)
                )
            except LuminReauthRequired:
                raise
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
