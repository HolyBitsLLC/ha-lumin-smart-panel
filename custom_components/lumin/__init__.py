"""The Lumin Smart Panel integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LuminApiClient, LuminPanel, LuminTokenManager
from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_PANELS,
    CONF_PANEL_GUID,
    CONF_PANEL_IP,
    CONF_PANEL_NAME,
    CONF_PANEL_LSP_ID,
    CONF_REFRESH_TOKEN,
    CONF_USE_CLOUD_FALLBACK,
    AUTH0_DOMAIN,
    AUTH0_CLIENT_ID,
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

    async def _on_tokens_refreshed(new_access: str, new_refresh: str) -> None:
        """Persist refreshed tokens to the config entry and update WS clients."""
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_ACCESS_TOKEN: new_access, CONF_REFRESH_TOKEN: new_refresh},
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
    )

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
