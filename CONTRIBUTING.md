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

## Commit messages

Releases are derived from commit messages, so they must follow
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):
`fix: ...`, `feat: ...`, `perf: ...`, and so on. A breaking change is `feat!:`,
`fix!:`, or any type with a `BREAKING CHANGE:` footer.

Commit types that cut a release on their own: `feat`, `fix`, `perf`, `deps`,
`revert`, plus any breaking change. Types that do not: `chore`, `ci`, `docs`,
`style`, `refactor`, `test`, `build`. A `docs` commit still appears in the
changelog of the next release, it just does not trigger one by itself.

## Release Process

Merging to `master` releases automatically (`.github/workflows/release.yaml`).
There is no release pull request to merge: the workflow reads the commits since
the last `v*` tag, computes the next version from them, writes
`custom_components/lumin/manifest.json`, `.release-please-manifest.json` and
`CHANGELOG.md`, commits that as `chore(release): vX.Y.Z`, and creates the tag and
GitHub release. HACS installs that release, so the tag and the manifest version
always have to match — the `validate` job checks it.

Versioning is pre-1.0 semantic versioning with `bump-minor-pre-major` and
`bump-patch-for-minor-pre-major` (see `release-please-config.json`, which is no
longer driven by release-please: the release script reads its `extra-files` and
bump flags directly): a breaking change bumps the minor, a feature or fix bumps
the patch.

`workflow_dispatch` on the Release workflow is the manual escape hatch: use it to
re-run a release that failed after the version commit landed, or to pass an
explicit `version`.

There is no Harbor payload image to build: since ADR-0162 the integration is
delivered through HACS from the GitHub release, not from an image the cluster
pulls.
