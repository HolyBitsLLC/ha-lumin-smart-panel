"""The Lumin Smart Panel integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    LuminApiClient,
    LuminAuthError,
    LuminConnectionError,
    LuminPanel,
    LuminReauthRequired,
    LuminTokenManager,
)
from .const import (
    AUTH0_CLIENT_ID,
    AUTH0_DOMAIN,
    CONF_PANEL_GUID,
    CONF_PANEL_IP,
    CONF_PANEL_LSP_ID,
    CONF_PANEL_NAME,
    CONF_PANELS,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_USE_CLOUD_FALLBACK,
    PLATFORMS,
)
from .coordinator import LuminDataCoordinator

_LOGGER = logging.getLogger(__name__)

type LuminConfigEntry = ConfigEntry[LuminDataCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LuminConfigEntry) -> bool:
    """Set up Lumin Smart Panel from a config entry."""
    session = async_get_clientsession(hass)
    token = entry.data[CONF_ACCESS_TOKEN]
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN, "")
    panels_config = entry.data.get(CONF_PANELS, [])
    use_cloud = entry.data.get(CONF_USE_CLOUD_FALLBACK, True)

    async def _on_tokens_refreshed(
        new_access: str, new_refresh: str, expires_at: float
    ) -> None:
        """Persist refreshed tokens to the config entry and update WS clients."""
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: new_access,
                CONF_REFRESH_TOKEN: new_refresh,
                CONF_TOKEN_EXPIRES_AT: expires_at,
            },
        )
        # Push new token to WebSocket clients (they need it for cloud reconnect)
        if entry.runtime_data:
            entry.runtime_data.update_ws_tokens(new_access)
        _LOGGER.debug("Persisted refreshed tokens to config entry")

    token_manager = LuminTokenManager(
        session=session,
        access_token=token,
        refresh_token=refresh_token,
        auth0_domain=AUTH0_DOMAIN,
        client_id=AUTH0_CLIENT_ID,
        on_tokens_refreshed=_on_tokens_refreshed,
        expires_at=entry.data.get(CONF_TOKEN_EXPIRES_AT),
    )

    # Recover the session *before* the first poll. The persisted access token is
    # normally past its lifetime after any meaningful restart; the refresh token
    # is not, so this one call is what makes a restart self-healing instead of
    # requiring the token to be pasted in again.
    try:
        await token_manager.async_startup_refresh()
    except LuminReauthRequired as err:
        if use_cloud:
            # Hand off to HA's reauth flow rather than retrying a dead grant.
            raise ConfigEntryAuthFailed(str(err)) from err
        # Local-only entry: the panel does not need a fresh cloud token, so keep
        # the stored one instead of blocking setup on a reauth prompt.
        _LOGGER.debug("Stored local-only token has no expiry path: %s", err)
    except (LuminConnectionError, LuminAuthError) as err:
        # Transient: let the first refresh report it and retry with backoff.
        _LOGGER.warning("Could not refresh the Lumin token at startup: %s", err)

    token = token_manager.access_token

    panels = []
    for pc in panels_config:
        panel = LuminPanel(
            ip_address=pc.get(CONF_PANEL_IP, ""),
            guid=pc[CONF_PANEL_GUID],
            name=pc.get(CONF_PANEL_NAME),
            lsp_id=pc.get(CONF_PANEL_LSP_ID),
        )
        panels.append(panel)

    client = LuminApiClient(
        session=session,
        access_token=token,
        panels=panels,
        use_cloud_fallback=use_cloud,
        token_manager=token_manager,
    )

    coordinator = LuminDataCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    # Start WebSocket connections for real-time power
    await coordinator.async_setup_websockets()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: LuminConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: LuminDataCoordinator = entry.runtime_data
    await coordinator.async_shutdown_websockets()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
