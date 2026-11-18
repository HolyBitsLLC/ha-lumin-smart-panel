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

Releases are two steps, and only the second one is manual.

1. A qualifying merge to `master` (a `feat`/`fix`/`perf`/`deps`/`revert`
   commit, or any breaking change) makes `.github/workflows/release.yaml`
   compute the next version from the commits since the last `v*` tag, write
   `custom_components/lumin/manifest.json`, `.release-please-manifest.json` and
   `CHANGELOG.md`, push a `release/vX.Y.Z` branch, and open a one-commit
   `chore(release): vX.Y.Z` pull request.
2. Merging that pull request makes the workflow tag the commit and publish the
   GitHub release. HACS installs that release, so the tag and the manifest
   version always have to match — the `validate` job checks it.

The manual cost is one approval click per release. Nothing else is by hand: the
version, the changelog and the file edits are all derived from the commits.

Versioning is pre-1.0 semantic versioning with `bump-minor-pre-major` and
`bump-patch-for-minor-pre-major` (read from `release-please-config.json`, which
is no longer driven by release-please: the release script reads its
`extra-files` and bump flags directly): a breaking change bumps the minor, a
feature or fix bumps the patch.

`workflow_dispatch` on the Release workflow is the manual escape hatch: re-run
it to tag a release whose PR merged but whose release is missing (the tag step
is idempotent), or pass an explicit `version`, or `dry-run` to see the plan
without creating a branch or PR.

`validate-hacs` runs on `ubuntu-latest` for every event: `hacs/action` is a
Docker container action and the self-hosted pool has no Docker daemon by design
(image builds use kaniko in Kubernetes).

### Why there is no bypass actor

The release PR exists only because the version commit has to touch `master`,
which requires 1 approving review, code-owner review and three status checks,
with `enforce_admins: false`. A bot cannot push there. The one thing that would
let it is a ruleset `bypass_actors` entry, and that trade is not worth it:

- `bypass_mode: "always"` is **unscoped**. It is not "may push a version bump"
  — it is "may write any commit to `master`, force-push it, and delete the
  branch, with no review and no status checks". The rules it would skip include
  `deletion` and `non_fast_forward`.
- The credential is App-wide. `agent-holybits`'s private key is installed on
  every repository the App is installed on and lives in Vaultwarden and on a
  shared multi-agent host (`~/.ssh/holybitsagent.pem`). Making it a
  `master`-write credential for this repo means every agent session on that host
  holds one, for this repository's `manifest.json` — the `requirements` line
  that HACS `pip install`s into every user's Home Assistant.
- There is no `Integration` bypass in this organisation today, and
  `agent-skills` has no bypass at all. This would be a new pattern, not an
  existing one.

`bypass_mode: "pull_request"` is the only narrower alternative, and it does not
solve the problem: per GitHub's docs it is for "grant[ing] bypass to an actor
without allowing them to push directly to a repository" — the actor must open a
PR "*and can then choose to bypass any branch protections and merge that pull
request*". So it would still be an unscoped "merge anything without review"
credential; it only trades a direct push for a self-merged PR. `exempt` is worse
again: rules are not run at all *and no bypass audit entry is created*.

Against that, the alternative costs one approval click per release.

### One-time setup

One piece, admin-only: add two repository secrets
(Settings → Secrets and variables → Actions → *Secrets*). No branch-protection
or ruleset change is required, and no bypass actor is added.

| Secret | Value |
| --- | --- |
| `APP_ID` | `4048591` — the `agent-holybits` GitHub App ID (from `GET /app`) |
| `APP_PRIVATE_KEY` | that App's private key, full PEM including the BEGIN/END lines |

Repository-level is sufficient: the workflow only needs an installation token
for *this* repository. An organisation secret works too but buys nothing — the
PEM is App-wide regardless of where it is stored, so its blast radius is the
App's installation set either way.

The token is requested with only `contents: write` and `pull-requests: write`.
It is deliberately **not** granted any protection bypass, so it can push a
release branch and open a PR, but it cannot write to `master` — that still needs
a human approval, exactly as for any other change.

Until the secrets exist the Release workflow fails **before** writing anything
(no branch, no PR, no tag), with the instruction above in the step summary.

### Appendix: the bypass ruleset, if that judgement is ever revisited

Not applied, and not recommended. Recorded so the work is not repeated. It
requires migrating `master` off classic branch protection (classic has no bypass
list, only `enforce_admins`), and rulesets *layer* with classic protection, so
leaving the classic rule in place keeps the App blocked — "the most restrictive
version of the rule applies".

```json
{
  "name": "protect-master",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] }
  },
  "bypass_actors": [
    { "actor_id": 4048591, "actor_type": "Integration", "bypass_mode": "always" }
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

`actor_id` is the **App ID** — for `actor_type: "Integration"` the API's
`actor_id` is the entity's own ID, the same way the `User` entries in our other
rulesets use the user's ID (harvester-argo `protect-main` is
`{actor_id: 6446939, actor_type: "User"}`, and `GET /users/claywd` returns
`6446939`). `required_signatures` is deliberately absent: the release commit is
created locally and pushed, so it is not signed.

```bash
gh api -X POST repos/HolyBitsLLC/ha-lumin-smart-panel/rulesets \
  --input protect-master.json
# then, in the same sitting — otherwise the App stays blocked:
gh api -X DELETE repos/HolyBitsLLC/ha-lumin-smart-panel/branches/master/protection
```

There is no Harbor payload image to build: since ADR-0162 the integration is
delivered through HACS from the GitHub release, not from an image the cluster
pulls.
