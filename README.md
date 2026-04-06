# Lumin Smart Panel - Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Local-first Home Assistant integration for [Lumin Smart Panels](https://www.luminsmart.com/) — relay-based electrical panel load management.

## Features

- **Local-first**: Communicates directly with panels on your LAN (port 443)
- **Cloud fallback**: Falls back to Lumin's cloud API when local is unavailable
- **Multi-panel support**: Manage multiple Lumin Smart Panels from one integration
- **Circuit switches**: Toggle individual circuit relays on/off
- **Power monitoring**: Real-time wattage per circuit and whole-panel total
- **Grid status**: Binary sensor for on-grid vs off-grid detection
- **"Everything Else"**: Unmonitored load calculated by the panel

## Entities Created

| Entity Type | Per-Panel | Description |
|---|---|---|
| `switch` | Per circuit | Toggle circuit relay on/off |
| `sensor` | Per circuit | Real-time power draw (W) |
| `sensor` | 1 | Total panel power consumption |
| `sensor` | 1 | "Everything Else" unmonitored load |
| `binary_sensor` | 1 | Panel connectivity (local reachable) |
| `binary_sensor` | 1 | Grid power status |

## Setup

### Prerequisites

- Lumin Smart Panel(s) on your local network
- Lumin account credentials
- Access token from the Lumin portal

### Getting Your Access Token

1. Go to https://portal.luminsmart.com
2. Log in with your Lumin account
3. Open browser DevTools (F12)
4. Go to **Application** → **Local Storage** → `portal.luminsmart.com`
5. Copy the `access_token` value

### Installation

#### HACS (Recommended)

1. Add this repository as a custom repository in HACS
2. Install "Lumin Smart Panel"
3. Restart Home Assistant
4. Add the integration via Settings → Devices & Services → Add Integration → Lumin Smart Panel

#### Manual

1. Copy `custom_components/lumin/` to your HA `custom_components/` directory
2. Restart Home Assistant
3. Add the integration via Settings → Devices & Services

### Configuration

Two setup paths:

1. **Account-based** (recommended): Enter your access token, the integration discovers your panels from the cloud, then you provide local IPs for direct communication.

2. **Manual**: Enter a panel IP address and access token directly. The integration probes the panel to get its GUID and circuit count.

## Architecture

```
Home Assistant
  └── Lumin Integration
       ├── Local Client (primary)
       │    └── https://{panel_ip}:443 (self-signed TLS)
       └── Cloud Client (fallback)
            └── https://api.luminsmart.com
```

The integration always tries the local API first. If the panel is unreachable on the LAN, it falls back to the Lumin cloud API (if enabled).

## Panel Info

- **Port 443**: Production REST API (requires Auth0 JWT)
- **Port 8085**: Setup wizard web UI
- **Port 80**: HTTP → HTTPS redirect
- **Port 22**: SSH (OpenSSH 8.9)
- **Firmware**: Linux armv7l, Mender OTA updates
