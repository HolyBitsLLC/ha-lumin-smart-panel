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

All PRs targeting `master` must pass the `lint`, `test` and `validate` jobs before merge. Pull requests run on GitHub-hosted runners; pushes to `master` run on the shared self-hosted pool.

## Release Process

Merging to `master` triggers release-please (`.github/workflows/release.yaml`).
It opens/updates a `chore(master): release X.Y.Z` PR; merging that PR creates the
GitHub release and tag, and bumps `custom_components/lumin/manifest.json` and
`.release-please-manifest.json` together. HACS installs that release, so the tag
and the manifest version always have to match — the `validate` job checks it.

There is no Harbor payload image to build: since ADR-0162 the integration is
delivered through HACS from the GitHub release, not from an image the cluster
pulls.
