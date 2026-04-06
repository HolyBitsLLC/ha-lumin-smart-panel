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
- **Automatic token refresh**: Auth0 refresh token keeps the integration authenticated indefinitely — no manual re-auth needed
- **Easy setup**: Paste the entire Local Storage JSON blob from the Lumin portal — the integration extracts tokens automatically

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

1. Go to https://portal.luminsmart.com and log in
2. Open browser DevTools (F12)
3. Go to **Application** → **Local Storage** → `portal.luminsmart.com`
4. Click the auth entry and **copy the entire Value field** (Ctrl+A, Ctrl+C)
5. Paste it into the integration's setup form — the integration extracts the access token and refresh token automatically

> **Tip**: You can paste the full JSON blob (5000+ chars), a truncated blob, the inner `body` object, or just a raw JWT access token. The parser handles all formats.

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
- **Token lifecycle**: Proactively refreshes Auth0 tokens 5 minutes before expiry; persists refreshed tokens to the config entry

## Panel Info

- **Port 443**: Production REST API (requires Auth0 JWT)
- **Port 8085**: WebSocket for real-time power readings (local, no auth)
- **Port 80**: HTTP → HTTPS redirect
- **Port 22**: SSH (OpenSSH 8.9)
- **Firmware**: Linux armv7l, Mender OTA updates

## Security

- Tokens are stored in HA's encrypted config entry storage — never in plaintext files
- Auth0 refresh tokens allow indefinite authentication without storing passwords
- Local WebSocket connections (port 8085) do not require authentication
- The integration uses self-signed TLS for local panel communication (certificate verification disabled for local connections only)
- See [SECURITY.md](SECURITY.md) for vulnerability reporting
