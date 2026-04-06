"""Config flow for Lumin Smart Panel."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LuminCloudClient, LuminLocalClient, LuminPanel, LuminAuthError
from .const import (
    DOMAIN,
    CONF_PANELS,
    CONF_PANEL_IP,
    CONF_PANEL_GUID,
    CONF_PANEL_NAME,
    CONF_PANEL_LSP_ID,
    CONF_REFRESH_TOKEN,
    CONF_USE_CLOUD_FALLBACK,
    AUTH0_DOMAIN,
    AUTH0_CLIENT_ID,
    AUTH0_AUDIENCE,
)

_LOGGER = logging.getLogger(__name__)


class LuminConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lumin Smart Panel."""

    VERSION = 1

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._panels: list[dict[str, Any]] = []
        self._selected_panels: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step - choose auth method."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["token", "manual_panel"],
        )

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle token-based authentication.

        Accepts either:
        1. The full JSON blob from portal Local Storage (auto-extracts tokens)
        2. A raw JWT access token string
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            raw = user_input.get("token_data", "").strip()
            token, refresh = self._parse_token_input(raw)

            if not token:
                errors["base"] = "invalid_token_format"
            else:
                session = async_get_clientsession(self.hass)
                cloud = LuminCloudClient(session, token)

                try:
                    panels = await cloud.get_panels()
                    if not panels:
                        errors["base"] = "no_panels"
                    else:
                        self._access_token = token
                        self._refresh_token = refresh
                        self._panels = panels
                        return await self.async_step_select_panels()
                except LuminAuthError:
                    errors["base"] = "invalid_auth"
                except Exception:
                    _LOGGER.exception("Unexpected error during auth")
                    errors["base"] = "unknown"

        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema(
                {
                    vol.Required("token_data"): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "auth0_domain": AUTH0_DOMAIN,
                "portal_url": "https://portal.luminsmart.com",
            },
        )

    @staticmethod
    def _parse_token_input(raw: str) -> tuple[str, str]:
        """Parse token input — accepts JSON blob or raw JWT.

        Auth0 Local Storage format:
          {"body":{"access_token":"...","refresh_token":"...",...}}
        Also accepts the inner body object directly:
          {"access_token":"...","refresh_token":"...",...}
        Or a plain JWT string starting with 'ey'.

        Returns (access_token, refresh_token). Empty strings on failure.
        """
        if not raw:
            return ("", "")

        # Try JSON parse first
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                # Handle {"body": {"access_token": "..."}} wrapper
                if "body" in data and isinstance(data["body"], dict):
                    data = data["body"]
                access = data.get("access_token", "")
                refresh = data.get("refresh_token", "")
                if access:
                    return (access, refresh)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
            return ("", "")

        # Plain JWT string (starts with ey = base64 of {"alg":...)
        if raw.startswith("ey") and "." in raw:
            return (raw, "")

        return ("", "")

    async def async_step_select_panels(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user select which panels to add."""
        if user_input is not None:
            selected = user_input.get("panels", [])
            self._selected_panels = [
                p for p in self._panels if str(p.get("id", "")) in selected
            ]
            return await self.async_step_panel_ips()

        panel_options = {
            str(p["id"]): f"{p.get('name', 'Panel')} (GUID: {p.get('guid', 'unknown')})"
            for p in self._panels
        }

        return self.async_show_form(
            step_id="select_panels",
            data_schema=vol.Schema(
                {
                    vol.Required("panels"): vol.All(
                        vol.Coerce(list),
                        [vol.In(panel_options)],
                    ),
                }
            ),
        )

    async def async_step_panel_ips(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect IP addresses for each selected panel for local access."""
        errors: dict[str, str] = {}

        if user_input is not None:
            panels_config = []
            for panel in self._selected_panels:
                guid = panel.get("guid", "")
                ip = user_input.get(f"ip_{guid}", "").strip()
                if ip:
                    # Validate connectivity
                    session = async_get_clientsession(self.hass)
                    test_panel = LuminPanel(ip, guid)
                    local = LuminLocalClient(session, test_panel, self._access_token)
                    connected = await local.test_connection()
                    if not connected:
                        _LOGGER.warning(
                            "Could not connect to panel %s at %s, "
                            "will use cloud fallback",
                            guid, ip,
                        )

                panels_config.append(
                    {
                        CONF_PANEL_GUID: guid,
                        CONF_PANEL_IP: ip,
                        CONF_PANEL_NAME: panel.get("name", f"Lumin {guid[-6:]}"),
                        CONF_PANEL_LSP_ID: panel.get("id"),
                    }
                )

            await self.async_set_unique_id(
                "_".join(p[CONF_PANEL_GUID] for p in panels_config)
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title="Lumin Smart Panels",
                data={
                    CONF_ACCESS_TOKEN: self._access_token,
                    CONF_REFRESH_TOKEN: self._refresh_token or "",
                    CONF_PANELS: panels_config,
                    CONF_USE_CLOUD_FALLBACK: True,
                },
            )

        # Build form asking for IP of each panel
        schema_dict = {}
        for panel in self._selected_panels:
            guid = panel.get("guid", "")
            name = panel.get("name", f"Panel {guid[-6:]}")
            schema_dict[vol.Optional(f"ip_{guid}", default="")] = str

        return self.async_show_form(
            step_id="panel_ips",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={
                "panel_count": str(len(self._selected_panels)),
            },
        )

    async def async_step_manual_panel(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manually add a panel by IP address without cloud auth."""
        errors: dict[str, str] = {}

        if user_input is not None:
            ip = user_input[CONF_PANEL_IP].strip()
            token = user_input[CONF_ACCESS_TOKEN].strip()
            name = user_input.get(CONF_PANEL_NAME, "").strip()

            session = async_get_clientsession(self.hass)

            # Probe panel info via local API on 443
            guid = "unknown"
            lsp_id = None
            panel = LuminPanel(ip, guid, name=name or None)
            local = LuminLocalClient(session, panel, token)

            try:
                panels = await local.get_panels()
                if panels:
                    dev = panels[0]
                    guid = dev.get("guid", guid)
                    lsp_id = dev.get("id")
                    panel.guid = guid
                    panel.lsp_id = lsp_id
                    if not name:
                        name = dev.get("name", f"Lumin {guid[-6:]}")
            except Exception:
                # Fall back to probing the setup page on 8085
                try:
                    import re
                    async with session.get(
                        f"http://{ip}:8085/",
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        text = await resp.text()
                        m = re.search(r"GUID:\s*(\w+)", text)
                        if m:
                            guid = m.group(1)
                except Exception:
                    _LOGGER.debug("Could not probe panel at %s", ip)

            if not await local.test_connection():
                errors["base"] = "cannot_connect"
            else:
                if not name:
                    name = f"Lumin {guid[-6:]}"

                await self.async_set_unique_id(guid)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_ACCESS_TOKEN: token,
                        CONF_REFRESH_TOKEN: "",
                        CONF_PANELS: [
                            {
                                CONF_PANEL_GUID: guid,
                                CONF_PANEL_IP: ip,
                                CONF_PANEL_NAME: name,
                                CONF_PANEL_LSP_ID: lsp_id,
                            }
                        ],
                        CONF_USE_CLOUD_FALLBACK: False,
                    },
                )

        return self.async_show_form(
            step_id="manual_panel",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PANEL_IP): str,
                    vol.Required(CONF_ACCESS_TOKEN): str,
                    vol.Optional(CONF_PANEL_NAME, default=""): str,
                }
            ),
            errors=errors,
        )
