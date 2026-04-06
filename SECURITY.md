# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public GitHub issue**
2. Email security concerns to the maintainer via the contact listed on the [HolyBitsLLC GitHub organization](https://github.com/HolyBitsLLC)
3. Include a clear description of the vulnerability and steps to reproduce

We will acknowledge receipt within 48 hours and provide a fix timeline.

## Scope

This integration communicates with:
- **Local Lumin panels** on your LAN (HTTPS port 443, WebSocket port 8085)
- **Lumin cloud API** at `api.luminsmart.com` (HTTPS)
- **Lumin WebSocket** at `ws.luminsmart.com` (WSS)
- **Auth0** at `auth.luminsmart.io` for token refresh (HTTPS)

Auth tokens are stored in Home Assistant's encrypted config entry storage and are never logged or exposed in plaintext.
