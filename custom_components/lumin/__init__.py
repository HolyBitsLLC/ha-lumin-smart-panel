"""The Lumin Smart Panel integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LuminApiClient, LuminPanel
from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_PANELS,
    CONF_PANEL_GUID,
    CONF_PANEL_IP,
    CONF_PANEL_NAME,
    CONF_PANEL_LSP_ID,
    CONF_USE_CLOUD_FALLBACK,
)
from .coordinator import LuminDataCoordinator

_LOGGER = logging.getLogger(__name__)

type LuminConfigEntry = ConfigEntry[LuminDataCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LuminConfigEntry) -> bool:
    """Set up Lumin Smart Panel from a config entry."""
    session = async_get_clientsession(hass)
    token = entry.data[CONF_ACCESS_TOKEN]
    panels_config = entry.data.get(CONF_PANELS, [])
    use_cloud = entry.data.get(CONF_USE_CLOUD_FALLBACK, True)

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
