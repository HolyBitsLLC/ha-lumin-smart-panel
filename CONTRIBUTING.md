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

`validate-hacs` runs on `ubuntu-latest` for every event: `hacs/action` is a
Docker container action and the self-hosted pool has no Docker daemon by design
(image builds use kaniko in Kubernetes).

### One-time admin setup

Two pieces, both admin-only. Until they exist the Release workflow fails before
creating a commit or a tag, and the only symptom is that no release is cut — it
never leaves a half-written state.

**1. Repository secrets** (Settings → Secrets and variables → Actions → *Secrets*):

| Secret | Value |
| --- | --- |
| `APP_ID` | `4048591` — the `agent-holybits` GitHub App ID (from `GET /app`) |
| `APP_PRIVATE_KEY` | that App's private key, full PEM including the BEGIN/END lines |

Repository-level is sufficient: the workflow only needs an installation token
for *this* repository. Use an organisation secret only if you want the same
credential shared with other repos — the PEM is App-wide either way, so the
blast radius is the App's installation set, not this repo.

**2. A branch ruleset with the App in `bypass_actors`.** An App installation
token is *not* a repository admin, and classic branch protection has no bypass
list — only `enforce_admins`. So while `master` uses classic protection, the
App's push is rejected with `GH006: 3 of 3 required status checks are expected`
(the same failure the old `sync-manifest` job hit). Create the ruleset below, then
**delete the classic branch protection rule for `master` in the same sitting**:
rulesets *layer* with classic protection and "the most restrictive version of
the rule applies", so leaving classic protection in place keeps the App blocked.

```json
{
  "name": "protect-master",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "bypass_actors": [
    {
      "actor_id": 4048591,
      "actor_type": "Integration",
      "bypass_mode": "always"
    }
  ],
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "require_last_push_approval": true,
        "required_review_thread_resolution": true
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "lint" },
          { "context": "test" },
          { "context": "validate" }
        ]
      }
    }
  ]
}
```

`actor_id` is the **App ID**, not the installation ID and not the bot user ID:
for `actor_type: "Integration"` the API's `actor_id` is the entity's own ID, the
same way the `User` bypass entries in our other rulesets use the user's ID.
Every parameter the API marks Required is present; the omitted optional ones
keep their permissive defaults (`allowed_merge_methods` defaults to all three,
so merge methods stay unrestricted exactly as today). Nothing is weakened
relative to today's classic rule: 1 approving review, code owner review,
stale-review dismissal, last-push approval, conversation resolution, the same
three required checks (strict), no force pushes, no deletion.
`required_signatures` is deliberately **not** added — the release commit is
created locally and pushed, so it is not signed, and adding that rule would only
be survivable via the App bypass.

Apply it with an admin token, then remove the classic rule **in the same
sitting** (rulesets and classic protection layer, and the stricter one wins):

```bash
# 1. create the ruleset (from the JSON above, saved as protect-master.json)
gh api -X POST repos/HolyBitsLLC/ha-lumin-smart-panel/rulesets \
  --input protect-master.json

# 2. retire the classic rule; until this runs, the App stays blocked
gh api -X DELETE repos/HolyBitsLLC/ha-lumin-smart-panel/branches/master/protection
```

This repository's Release job does **not** gate on CI: it runs on the push and
pushes its own version-only commit (three version/changelog files) through the
App bypass. That is a deliberate trade-off — the reviewed PR already passed
`lint`/`test`/`validate`, and waiting for the push's own checks would mean
polling for many minutes inside the release run. A red `validate-hacs` on the
release commit would therefore be cosmetic rather than blocking, which is why
that job is pinned to a runner that can actually execute it.

There is no Harbor payload image to build: since ADR-0162 the integration is
delivered through HACS from the GitHub release, not from an image the cluster
pulls.
