"""Compute and apply the next release version for the `lumin` integration.

This repository releases on every qualifying merge to `master`, so the version
number, `custom_components/lumin/manifest.json`, `.release-please-manifest.json`
and `CHANGELOG.md` must be derived from git history rather than from a release
pull request.

Why not release-please? Its publish step (`Manifest.createReleases`) only tags
pull requests that a human *merged*; with `skip-github-pull-request` no release
pull request is ever opened, so nothing is ever tagged and no version file is
ever written. See the comment in `.github/workflows/release.yaml`.

Usage:

    release.py plan  [--repo-root .] [--revision HEAD]
        Print `key=value` lines describing the next release (for $GITHUB_OUTPUT).

    release.py apply --version X.Y.Z [--repo-root .] [--revision HEAD]
        Rewrite the version files and prepend a CHANGELOG entry.

Versioning follows release-please's `DefaultVersioningStrategy` with the
`bump-minor-pre-major` / `bump-patch-for-minor-pre-major` flags read from
`release-please-config.json`, so the switch from the release-PR model does not
change how commits map to versions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

CONFIG_PATH = "release-please-config.json"
MANIFEST_PATH = ".release-please-manifest.json"
CHANGELOG_PATH = "CHANGELOG.md"

TAG_PREFIX = "v"

# Types that cut a release on their own. `docs` is deliberately excluded: it is
# still listed in the changelog of the *next* release, but a README typo should
# not push an update prompt to every HACS user. `chore`, `ci`, `style`,
# `refactor`, `test` and `build` neither release nor appear in the changelog
# (release-please's `python` strategy treats those sections as hidden).
RELEASE_TYPES = {"feat", "fix", "perf", "deps", "revert"}

# Visible section headings, in the order release-please emits them.
SECTIONS = [
    ("feat", "Features"),
    ("fix", "Bug Fixes"),
    ("perf", "Performance Improvements"),
    ("deps", "Dependencies"),
    ("revert", "Reverts"),
    ("docs", "Documentation"),
]

COMMIT_RE = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?: (?P<desc>.+)$"
)
PR_SUFFIX_RE = re.compile(r"\s*\(#(\d+)\)\s*$")
BREAKING_NOTE_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def parse_version(text: str) -> tuple[int, int, int]:
    match = VERSION_RE.match(text.strip())
    if not match:
        raise SystemExit(f"not a semver release version: {text!r}")
    return (int(match["major"]), int(match["minor"]), int(match["patch"]))


def read_config(root: Path) -> dict:
    with open(root / CONFIG_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def last_tag(root: Path, revision: str) -> str | None:
    """Newest `v*` tag reachable from `revision`.

    `--match` matters: the retired Harbor image pipeline left an `image-0.1.0`
    tag in the repository, and it must never be treated as a release baseline.
    """
    try:
        return git(
            "describe", "--tags", "--abbrev=0", f"--match={TAG_PREFIX}*", revision,
            cwd=root,
        ).strip()
    except subprocess.CalledProcessError:
        return None


def git_commits(root: Path, revision: str, base: str | None) -> list[str]:
    """Commit messages in `base..revision`, oldest first, split into records."""
    rev_range = f"{base}..{revision}" if base else revision
    raw = git("log", "--format=%H%x1f%s%x1f%b%x1e", rev_range, cwd=root)
    return [record for record in raw.split("\x1e") if record.strip()]


def parse_commits(records: list[str]) -> list[dict]:
    commits = []
    for record in records:
        # `git log` separates entries with a newline after the %x1e marker, so
        # strip before splitting or the leading newline lands inside the SHA.
        record = record.strip()
        if not record:
            continue
        sha, _, rest = record.partition("\x1f")
        subject, _, body = rest.partition("\x1f")
        record_match = COMMIT_RE.match(subject.strip())
        if not record_match:
            # Non-conventional subjects (merge commits, release commits) never
            # reach the changelog, exactly like release-please.
            continue
        breaking = bool(record_match["breaking"]) or bool(
            BREAKING_NOTE_RE.search(body or "")
        )
        desc = record_match["desc"]
        pr = PR_SUFFIX_RE.search(desc)
        commits.append(
            {
                "sha": sha,
                "type": record_match["type"].lower(),
                "scope": record_match["scope"],
                "breaking": breaking,
                "desc": PR_SUFFIX_RE.sub("", desc) if pr else desc,
                "pr": int(pr.group(1)) if pr else None,
            }
        )
    return commits


def next_version(
    current: tuple[int, int, int], commits: list[dict], package: dict
) -> tuple[int, int, int]:
    """Mirror release-please's DefaultVersioningStrategy.

    Breaking -> major, feat -> minor, anything else -> patch; pre-1.0 with
    `bump-minor-pre-major` instead bumps the minor for a breaking change, and
    with `bump-patch-for-minor-pre-major` bumps the patch for a feature.
    """
    bump_minor_pre_major = package.get("bump-minor-pre-major", False)
    bump_patch_for_minor_pre_major = package.get("bump-patch-for-minor-pre-major", False)

    breaking = sum(1 for commit in commits if commit["breaking"])
    features = sum(1 for commit in commits if commit["type"] in {"feat", "feature"})
    major, minor, patch = current
    pre_major = major == 0

    if breaking:
        return (major, minor + 1, 0) if pre_major and bump_minor_pre_major else (major + 1, 0, 0)
    if features:
        return (major, minor, patch + 1) if pre_major and bump_patch_for_minor_pre_major else (major, minor + 1, 0)
    return (major, minor, patch + 1)


def release_reason(commits: list[dict]) -> str:
    counted: dict[str, int] = {}
    for commit in commits:
        if commit["breaking"] or commit["type"] in RELEASE_TYPES:
            counted[commit["type"]] = counted.get(commit["type"], 0) + 1
    if not counted:
        return "no release-worthy commits (chore/ci/docs/style/refactor/test/build only)"
    return ", ".join(f"{count} {kind}" for kind, count in sorted(counted.items()))


def is_release_worthy(commits: list[dict]) -> bool:
    return any(
        commit["breaking"] or commit["type"] in RELEASE_TYPES for commit in commits
    )


def read_version_file(root: Path, jsonpath: str, path: str) -> str:
    with open(root / path, encoding="utf-8") as handle:
        data = json.load(handle)
    node = data
    for key in jsonpath_keys(jsonpath):
        if not isinstance(node, dict) or key not in node:
            raise SystemExit(f"{path}: no value at {jsonpath}")
        node = node[key]
    return str(node)


def jsonpath_keys(jsonpath: str) -> list[str]:
    """Minimal `$.a.b` JSONPath support: enough for `extra-files` entries here."""
    if not jsonpath.startswith("$"):
        raise SystemExit(f"only absolute JSONPaths are supported, got {jsonpath!r}")
    return [key for key in jsonpath[1:].split(".") if key]


def bump_extra_file(root: Path, path: str, jsonpath: str, new_version: str) -> bool:
    """Rewrite one JSON version field in place, preserving the file's formatting.

    Re-serialising the whole file would reformat every array in manifest.json
    on each release; a targeted textual replacement keeps the diff to one line.
    """
    keys = jsonpath_keys(jsonpath)
    if not keys:
        raise SystemExit(f"{path}: unusable JSONPath {jsonpath!r}")
    old_version = read_version_file(root, jsonpath, path)
    if old_version == new_version:
        return False
    field = keys[-1]
    target = root / path
    text = target.read_text(encoding="utf-8")
    pattern = re.compile(
        r'("' + re.escape(field) + r'"\s*:\s*")' + re.escape(old_version) + r'(")'
    )
    replaced, count = pattern.subn(rf"\g<1>{new_version}\g<2>", text, count=1)
    if count != 1:
        raise SystemExit(
            f"{path}: could not locate {field} == {old_version!r} for a single, "
            "format-preserving replacement"
        )
    target.write_text(replaced, encoding="utf-8")
    return True


def bump_released_version(root: Path, new_version: str) -> bool:
    target = root / MANIFEST_PATH
    text = target.read_text(encoding="utf-8")
    old_version = json.loads(text)["."]
    if old_version == new_version:
        return False
    replaced, count = re.subn(
        r'("\."\s*:\s*")' + re.escape(old_version) + r'(")',
        rf"\g<1>{new_version}\g<2>",
        text,
        count=1,
    )
    if count != 1:
        raise SystemExit(
            f"{MANIFEST_PATH}: could not find a single \".\" == {old_version!r} entry"
        )
    target.write_text(replaced, encoding="utf-8")
    return True


def changelog_section(
    version: str, previous: str | None, commits: list[dict], repo: str, date: str
) -> str:
    compare = (
        f"https://github.com/{repo}/compare/{TAG_PREFIX}{previous}..."
        f"{TAG_PREFIX}{version}"
        if previous
        else f"https://github.com/{repo}/releases/tag/{TAG_PREFIX}{version}"
    )
    heading = (
        f"## [{version}]({compare}) ({date})"
        if previous
        else f"## {version} ({date})"
    )
    lines = [heading, "", ""]

    breaking = [commit for commit in commits if commit["breaking"]]
    if breaking:
        lines += ["### ⚠ BREAKING CHANGES", ""]
        lines += [f"* {commit['desc']}" for commit in breaking]
        lines += [""]

    for kind, title in SECTIONS:
        entries = [c for c in commits if c["type"] == kind]
        if not entries:
            continue
        lines += [f"### {title}", ""]
        for commit in entries:
            scope = f"**{commit['scope']}:** " if commit["scope"] else ""
            refs = [
                f"[{commit['sha'][:7]}](https://github.com/{repo}/commit/{commit['sha']})"
            ]
            if commit["pr"]:
                refs.insert(0, f"[#{commit['pr']}](https://github.com/{repo}/pull/{commit['pr']})")
            lines.append(f"* {scope}{commit['desc']} ({', '.join(refs)})")
        lines += [""]

    return "\n".join(lines).rstrip("\n") + "\n"


def prepend_changelog(root: Path, section: str, version: str) -> bool:
    target = root / CHANGELOG_PATH
    text = target.read_text(encoding="utf-8")
    if re.search(rf"^## \[?{re.escape(version)}\]?[ (]", text, re.MULTILINE):
        return False
    header = "# Changelog\n\n"
    if not text.startswith(header):
        raise SystemExit(f"{CHANGELOG_PATH}: unexpected header, refusing to edit")
    target.write_text(header + section + "\n" + text[len(header):], encoding="utf-8")
    return True


def cmd_plan(args: argparse.Namespace, root: Path) -> int:
    config = read_config(root)
    package = config["packages"]["."]
    base_tag = last_tag(root, args.revision)
    previous = parse_version(base_tag[len(TAG_PREFIX):]) if base_tag else (0, 0, 0)
    commits = parse_commits(git_commits(root, args.revision, base_tag))
    should_release = is_release_worthy(commits)
    version = ".".join(str(part) for part in next_version(previous, commits, package))

    print(f"release={'true' if should_release else 'false'}")
    print(f"version={version}")
    print(f"previous_version={'.'.join(str(part) for part in previous)}")
    print(f"last_tag={base_tag or ''}")
    print(f"tag={TAG_PREFIX}{version}")
    print(f"reason={release_reason(commits)}")
    return 0


def cmd_apply(args: argparse.Namespace, root: Path) -> int:
    config = read_config(root)
    package = config["packages"]["."]
    new_version = args.version.strip()
    parse_version(new_version)

    base_tag = last_tag(root, args.revision)
    previous = base_tag[len(TAG_PREFIX):] if base_tag else None
    commits = parse_commits(git_commits(root, args.revision, base_tag))

    changed = []
    for entry in package.get("extra-files", []):
        if isinstance(entry, str):
            if entry.endswith(".json"):
                if bump_extra_file(root, entry, "$.version", new_version):
                    changed.append(entry)
            else:
                raise SystemExit(f"{CONFIG_PATH}: unsupported extra-file {entry!r}")
            continue
        if entry.get("type") != "json":
            raise SystemExit(
                f"{CONFIG_PATH}: only `type: json` extra-files are supported, "
                f"got {entry.get('type')!r}"
            )
        if bump_extra_file(root, entry["path"], entry["jsonpath"], new_version):
            changed.append(entry["path"])

    if bump_released_version(root, new_version):
        changed.append(MANIFEST_PATH)

    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise SystemExit("set --repo or GITHUB_REPOSITORY")
    date = args.date or datetime.now(UTC).strftime("%Y-%m-%d")
    section = changelog_section(new_version, previous, commits, repo, date)
    if prepend_changelog(root, section, new_version):
        changed.append(CHANGELOG_PATH)

    print(f"changed={','.join(changed) if changed else 'none'}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--revision",
        default="HEAD",
        help="commit to release (default HEAD; use the tag's parent when "
        "recovering a release that failed after the bump landed)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("plan", help="print the next release as key=value")

    apply_parser = subparsers.add_parser("apply", help="write the version files")
    apply_parser.add_argument("--version", required=True)
    apply_parser.add_argument("--repo", default="")
    apply_parser.add_argument("--date", default="")

    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    if args.command == "plan":
        return cmd_plan(args, root)
    return cmd_apply(args, root)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
