# Lumin Smart Panel - Home Assistant Integration

[![CI](https://img.shields.io/github/actions/workflow/status/HolyBitsLLC/ha-lumin-smart-panel/ci.yaml?branch=master&label=CI)](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/actions/workflows/ci.yaml)
[![Release](https://img.shields.io/github/v/release/HolyBitsLLC/ha-lumin-smart-panel)](https://github.com/HolyBitsLLC/ha-lumin-smart-panel/releases)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Local-first Home Assistant integration for [Lumin Smart Panels](https://www.luminsmart.com/) — relay-based electrical panel load management with real-time WebSocket power monitoring and Energy dashboard support.

## Features

- **Local-first communication**: Talks directly to panels on your LAN via HTTPS (port 443), with automatic cloud fallback
- **Real-time power via WebSocket**: Live per-circuit wattage updated every 1–2 seconds (local ws://panel:8085 or cloud wss://ws.luminsmart.com)
- **Energy dashboard ready**: Per-circuit and whole-panel kWh sensors with `total_increasing` state class for HA's Energy dashboard
- **Multi-panel support**: Discover and manage multiple Lumin Smart Panels from one integration
- **Circuit control**: Toggle individual circuit relays on/off
- **Automatic token refresh**: the stored Auth0 refresh token is exchanged for a new access token before expiry, on any 401, and at startup — so a Home Assistant restart after downtime recovers without re-entering anything
- **Reauth flow**: if the refresh token itself is revoked (e.g. the Lumin password changed), Home Assistant prompts once for fresh token data instead of silently degrading
- **Easy setup**: paste the entire Local Storage JSON blob from the Lumin portal — the integration extracts tokens automatically

## Entities Created

### Per Circuit

| Entity Type | Entity ID Pattern | Description |
|---|---|---|
| `switch` | `switch.lumin_{name}` | Toggle circuit relay on/off (non-main circuits only) |
| `sensor` | `sensor.lumin_{name}_power` | Real-time power draw (W) via WebSocket |
| `sensor` | `sensor.lumin_{name}_energy` | Accumulated energy consumption (kWh) — Energy dashboard eligible |
| `sensor` | `sensor.lumin_{name}_peak_power` | Historical max power observed (W) |
| `binary_sensor` | `binary_sensor.lumin_{name}_active` | Circuit active/energized state |

### Per Panel

| Entity Type | Entity ID Pattern | Description |
|---|---|---|
| `sensor` | `sensor.lumin_{panel}_total_power` | Sum of all circuit power (W) — real-time whole-panel gauge |
| `sensor` | `sensor.lumin_{panel}_total_energy` | Accumulated whole-panel energy (kWh) — use for Grid Consumption |
| `binary_sensor` | `binary_sensor.lumin_{panel}_connectivity` | Panel reachable on local network |

## Energy Dashboard Setup

1. Go to **Settings → Dashboards → Energy**
2. Under **Grid Consumption**, add the `{Panel} Total Energy` sensor
3. Under **Individual Devices**, add per-circuit `{Circuit} Energy` sensors
4. The integration accumulates kWh from live WebSocket power readings using trapezoidal integration
5. Totals persist across HA restarts via `RestoreEntity`

## Setup

### Prerequisites

- Lumin Smart Panel(s) on your local network
- Lumin account (free, created during panel installation)

### Getting Your Token Data

1. Go to https://app.luminsmart.com and log in
2. Open browser DevTools (F12)
3. Go to **Application** → **Local Storage** → `app.luminsmart.com`
4. Click the auth entry and **copy the entire Value field** (Ctrl+A, Ctrl+C)
5. Paste it into the integration's setup form — the integration extracts the access token and refresh token automatically

> **Tip**: You can paste the full JSON blob (5000+ chars), a truncated blob, the inner `body` object, or just a raw JWT access token. The parser handles all formats.

This is normally a **one-time** step. The refresh token in the blob is the durable
credential: the integration uses it to mint new access tokens proactively, on any
401, and at every startup, so a Home Assistant restart or a long outage recovers
by itself. See [Token lifecycle](#token-lifecycle) for the one case that still
needs a human.

### Installation

#### HACS (Recommended)

1. Add this repository as a custom repository in HACS:
   - URL: `https://github.com/HolyBitsLLC/ha-lumin-smart-panel`
   - Category: Integration
2. Install "Lumin Smart Panel"
3. Restart Home Assistant
4. Go to **Settings → Devices & Services → Add Integration → Lumin Smart Panel**

#### Manual

1. Copy `custom_components/lumin/` to your HA `custom_components/` directory
2. Restart Home Assistant
3. Add the integration via **Settings → Devices & Services**

### Configuration Flow

1. **Choose method**: "Authenticate with Lumin account" (recommended) or "Add panel manually by IP"
2. **Paste token data**: The full Local Storage JSON blob from the portal
3. **Select panels**: Choose which Lumin panels to add (multi-select)
4. **Enter panel IPs**: Provide local IP addresses for direct communication (optional — leave blank for cloud-only)

## Architecture

```
Home Assistant
  └── Lumin Integration
       ├── Token Manager (Auth0 auto-refresh)
       ├── Data Coordinator (REST polling, 30s interval)
       ├── WebSocket Clients (real-time power, per panel)
       │    ├── Local:  ws://{panel_ip}:8085/ws (preferred, no auth)
       │    └── Cloud:  wss://ws.luminsmart.com:50055/ws (fallback)
       ├── Local REST Client
       │    └── https://{panel_ip}:443/v2/lsps/* (self-signed TLS)
       └── Cloud REST Client
            └── https://api.luminsmart.com/v2/lsps/*
```

- **Local-first**: Always tries the local panel API first, falls back to cloud if unreachable
- **WebSocket reconnect**: Auto-reconnects with exponential backoff (2s → 60s)
- **Token lifecycle**: Refreshes the Auth0 access token 5 minutes before expiry, on any 401, and once at startup; the absolute expiry is persisted in the config entry so a restart knows the token is stale

## Token lifecycle

```
config entry (.storage)
  access_token   short-lived JWT      ← refreshed often
  refresh_token  durable grant        ← rotated on every refresh, persisted
  token_expires_at  absolute epoch    ← survives a restart
```

1. **At startup** the integration compares the persisted expiry (or the access
   token's own `exp` claim) against the clock. If it is stale it refreshes once,
   before the first poll. This is what makes an HA restart self-healing.
2. **Before expiry** a refresh happens up to 5 minutes early.
3. **On a 401** from either the local panel or the cloud API, the request is
   retried once with a freshly refreshed token. Concurrent refreshes are
   serialised, so Auth0's refresh-token rotation never sees a reused token.
4. **If the refresh token is dead** (`invalid_grant`, e.g. the Lumin account
   password changed) the coordinator raises `ConfigEntryAuthFailed`, Home
   Assistant raises a Repairs issue and starts a reauth flow, and only that
   prompt asks for new token data.

The two Lumin clients issue *different* refresh tokens. A blob copied from the
portal (`app.luminsmart.com`, client `un7psu3o3AZdd1cO1fcFSwqsQQgeVIXD`) is
bound to the portal client, while the mobile app's tokens belong to
`eKr5fjYckpRJbP3iqywCZXapDZbB3Kgs`; Auth0 answers `invalid_grant` when a token is
presented by the wrong client. The integration therefore reads the issuer from
the access token's `azp` claim and uses it first, falling back to the other known
client id once. Without that, a token pasted from the portal would refresh with
the mobile client id and look like a dead grant.

### Why not username/password?

Deriving the token from the Lumin email/password is not possible without a human
solving a CAPTCHA:

- Auth0's `password` and `password-realm` grants are **disabled** for both Lumin
  clients (`eKr5fjYckpRJbP3iqywCZXapDZbB3Kgs`, `un7psu3o3AZdd1cO1fcFSwqsQQgeVIXD`) —
  `POST https://auth.luminsmart.io/oauth/token` answers
  `403 {"error":"unauthorized_client","error_description":"Grant type 'password' not allowed for the client."}`.
- The only credential login is the portal's Auth0 login page, which always
  renders and submits a bot-detection CAPTCHA
  (`webAuth.renderCaptcha(...)` / `captcha: this.captcha.getValue()` in
  `https://auth.luminsmart.io/login`). Posting credentials without one returns
  `401 {"code":"invalid_captcha"}`.
- The tenant advertises an RFC 8628 device-authorization endpoint, but both
  Lumin clients reject the device grant
  (`403 {"error":"unauthorized_client","error_description":"Grant type 'urn:ietf:params:oauth:grant-type:device_code' not allowed for the client."}`),
  so the "enter a code on a web page" flow is not available either.
- Neither client allows a Home Assistant redirect URI: `/authorize` with
  `redirect_uri=http://localhost:8123/auth/external/callback` returns
  `403 Callback URL mismatch`. Only `https://app.luminsmart.com` is registered.

So the integration deliberately does not store the account password. The
refresh token is the machine credential, and the reauth flow is the fallback
when Lumin revokes it.

## Panel Info

- **Port 443**: Production REST API (requires Auth0 JWT)
- **Port 8085**: WebSocket for real-time power readings (local, no auth)
- **Port 80**: HTTP → HTTPS redirect
- **Port 22**: SSH (OpenSSH 8.9)
- **Firmware**: Linux armv7l, Mender OTA updates

## Security

- Tokens live in the Home Assistant config entry, which is plain JSON in
  `.storage/core.config_entries` on the config volume — it is not encrypted, so
  treat the HA `/config` volume as credential material (it already holds every
  other integration's secrets)
- The Lumin account password is never stored; the Auth0 refresh token is the
  machine credential and is rotated on every refresh
- Local WebSocket connections (port 8085) do not require authentication
- The integration uses self-signed TLS for local panel communication (certificate verification disabled for local connections only)
- See [SECURITY.md](SECURITY.md) for vulnerability reporting
