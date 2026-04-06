# Contributing to ha-lumin-smart-panel

## Development Setup

1. Clone the repo into your HA `custom_components/` directory (or symlink it):
   ```bash
   git clone git@github.com:HolyBitsLLC/ha-lumin-smart-panel.git
   ln -s $(pwd)/ha-lumin-smart-panel/custom_components/lumin /config/custom_components/lumin
   ```
2. Restart Home Assistant to load the integration.

## Linting

```bash
pip install ruff
ruff check .
```

## Testing

```bash
pip install pytest pytest-cov aiohttp
pytest --cov=custom_components/lumin -v
```

## CI

All PRs targeting `master` must pass the `lint` and `test` jobs before merge. CI runs on self-hosted runners.

## Release Process

Push or merge to `master`. The init container in the cluster deployment pulls the latest `master` on every pod restart.
