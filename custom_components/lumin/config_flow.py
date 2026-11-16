"""Config flow for Lumin Smart Panel."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .api import (
    LuminAuthError,
    LuminCloudClient,
    LuminConnectionError,
    LuminLocalClient,
    LuminPanel,
    jwt_expiry,
)
from .const import (
    AUTH0_DOMAIN,
    CONF_PANEL_GUID,
    CONF_PANEL_IP,
    CONF_PANEL_LSP_ID,
    CONF_PANEL_NAME,
    CONF_PANELS,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_USE_CLOUD_FALLBACK,
    DOMAIN,
)
from .token_input import parse_token_input

_LOGGER = logging.getLogger(__name__)


class LuminConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lumin Smart Panel."""

    VERSION = 1

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float | None = None
        self._panels: list[dict[str, Any]] = []
        self._selected_panels: list[dict[str, Any]] = []
        self._reauth_entry_id: str | None = None

    async def _async_accept_token_input(self, raw: str) -> str | None:
        """Validate one pasted value and keep the tokens for the remaining steps.

        Returns an ``errors["base"]`` key on failure, or ``None`` on success. The
        parse itself is in ``token_input.parse_token_input`` so it can be unit
        tested without Home Assistant installed.
        """
        parsed = parse_token_input(raw)
        _LOGGER.debug(
            "Parsed token input: length=%d, refreshable=%s, error=%s",
            len(raw),
            parsed.refreshable,
            parsed.error,
        )
        if parsed.error:
            return parsed.error

        session = async_get_clientsession(self.hass)
        try:
            panels = await LuminCloudClient(session, parsed.access).get_panels()
        except LuminAuthError as err:
            _LOGGER.warning("Lumin rejected the supplied token: %s", err)
            return "invalid_auth"
        except LuminConnectionError as err:
            _LOGGER.error("Connection error during panel fetch: %s", err)
            return "cannot_connect"
        except Exception:  # anything else becomes the generic `unknown` error
            _LOGGER.exception("Unexpected error while validating Lumin token data")
            return "unknown"

        if not panels:
            _LOGGER.warning("Token valid but no panels found on account")
            return "no_panels"

        self._access_token = parsed.access
        self._refresh_token = parsed.refresh
        self._expires_at = jwt_expiry(parsed.access)
        self._panels = panels
        return None

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

        Accepts the full Local Storage JSON blob, the inner body object, or a
        bare access token. A bare token is accepted but confirmed first, because
        it has no refresh grant and dies within about a day.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await self._async_accept_token_input(
                user_input.get("token_data", "")
            )
            if error:
                errors["base"] = error
            elif self._refresh_token:
                return await self.async_step_select_panels()
            else:
                return await self.async_step_access_token_only()

        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema(
                {
                    vol.Required("token_data"): TextSelector(
                        TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "auth0_domain": AUTH0_DOMAIN,
                # The live SPA is app.luminsmart.com. This placeholder renders
                # into the step-1 help text ("Go to {portal_url} and log in"),
                # and the same string already says the token lives in the
                # app.luminsmart.com local storage — pointing the login step at
                # the older portal host sent users to a different origin whose
                # local storage does not hold the token. async_step_reauth_confirm
                # already used app.luminsmart.com; this makes the two agree.
                "portal_url": "https://app.luminsmart.com",
            },
        )

    async def async_step_access_token_only(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Make the user acknowledge a token that cannot be refreshed.

        A bare access token works until it expires (Auth0 issues ~24h tokens) and
        carries no refresh grant, so Home Assistant will have to ask for new
        token data again. Creating that entry without saying so is what makes the
        "paste the key" path look like it worked and then fail a day later.
        """
        if user_input is not None:
            if self._reauth_entry_id is not None:
                return self._async_finish_reauth()
            return await self.async_step_select_panels()

        return self.async_show_form(
            step_id="access_token_only",
            data_schema=vol.Schema({}),
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Start reauth when the stored grant can no longer be refreshed.

        Home Assistant calls this after the coordinator raises
        `ConfigEntryAuthFailed`. The panel list and IP addresses are preserved;
        only the token data is replaced.
        """
        self._reauth_entry_id = self.context.get("entry_id")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Ask for fresh token data and swap it into the existing entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await self._async_accept_token_input(
                user_input.get("token_data", "")
            )
            if error:
                errors["base"] = error
            elif self._refresh_token:
                return self._async_finish_reauth()
            else:
                return await self.async_step_access_token_only()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("token_data"): TextSelector(
                        TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "portal_url": "https://app.luminsmart.com",
            },
        )

    def _async_finish_reauth(self) -> config_entries.ConfigFlowResult:
        """Write the validated tokens into the existing entry and reload it."""
        entry = self.hass.config_entries.async_get_entry(self._reauth_entry_id or "")
        if entry is None:
            return self.async_abort(reason="reauth_failed")
        return self.async_update_reload_and_abort(
            entry,
            data_updates={
                CONF_ACCESS_TOKEN: self._access_token or "",
                CONF_REFRESH_TOKEN: self._refresh_token or "",
                CONF_TOKEN_EXPIRES_AT: self._expires_at or 0.0,
            },
            reason="reauth_successful",
        )

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

        panel_options = [
            SelectOptionDict(
                value=str(p["id"]),
                label=f"{p.get('name', 'Panel')} (GUID: {p.get('guid', 'unknown')})",
            )
            for p in self._panels
        ]

        return self.async_show_form(
            step_id="select_panels",
            data_schema=vol.Schema(
                {
                    vol.Required("panels"): SelectSelector(
                        SelectSelectorConfig(
                            options=panel_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
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
                    CONF_TOKEN_EXPIRES_AT: self._expires_at or 0.0,
                    CONF_PANELS: panels_config,
                    CONF_USE_CLOUD_FALLBACK: True,
                },
            )

        # Build form asking for IP of each panel
        schema_dict = {}
        for panel in self._selected_panels:
            guid = panel.get("guid", "")
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
            except Exception:  # noqa: BLE001 - best-effort probe of an untrusted panel
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
                except Exception:  # noqa: BLE001 - probe is best-effort, never fatal
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
                        CONF_TOKEN_EXPIRES_AT: jwt_expiry(token) or 0.0,
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
