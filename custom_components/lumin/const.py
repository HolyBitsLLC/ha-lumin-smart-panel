"""Constants for the Lumin Smart Panel integration."""

DOMAIN = "lumin"

# Auth0 production config (public client - safe to embed)
AUTH0_DOMAIN = "auth.luminsmart.io"
AUTH0_CLIENT_ID = "eKr5fjYckpRJbP3iqywCZXapDZbB3Kgs"  # Mobile app client
AUTH0_CLIENT_ID_PORTAL = "un7psu3o3AZdd1cO1fcFSwqsQQgeVIXD"  # Portal client
AUTH0_AUDIENCE = "https://api.luminsmart.com"
AUTH0_CONNECTION = "Lumin"

# API endpoints
CLOUD_API_BASE = "https://api.luminsmart.com"
CLOUD_WS_URL = "wss://ws.luminsmart.com:50055/ws"
LOCAL_WS_PORT = 8085

# Polling — circuit state doesn't change rapidly; 30s is responsive enough
DEFAULT_SCAN_INTERVAL = 30

# Config keys
CONF_PANELS = "panels"
CONF_PANEL_IP = "panel_ip"
CONF_PANEL_GUID = "panel_guid"
CONF_PANEL_NAME = "panel_name"
CONF_PANEL_LSP_ID = "lsp_id"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_USE_CLOUD_FALLBACK = "use_cloud_fallback"

# Platforms
PLATFORMS = ["switch", "sensor", "binary_sensor"]
