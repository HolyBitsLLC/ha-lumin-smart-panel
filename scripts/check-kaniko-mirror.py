"""Fail when the inlined Kaniko job drifts from its pinned mirror point.

The delivery image is built by a Kaniko Job that is *inlined* into
``.github/workflows/publish-image.yaml`` because a public repository cannot call
a reusable workflow that lives in the private ``HolyBitsLLC/harvester-argo``
repository. The inlined Job mirrors ``reusable-kaniko-build.yaml``'s
``kaniko-build`` job, single-platform path.

``.github/kaniko-mirror.json`` records which file at which commit is mirrored,
plus the exact Kaniko flags, images and Job skeleton the inline copy must carry.
This script compares the workflow against that record: any added, removed or
renamed flag, image or skeleton field fails the check.

What this proves and what it does not:
  * It proves the inline copy is unchanged since the pin was recorded.
  * It CANNOT prove the private reusable has not changed upstream. A public
    repository's ``github.token`` is scoped to that repository, so CI here
    cannot read ``harvester-argo``, and no cross-repo credential belongs in a
    public repository. Upstream drift is caught when a human refreshes the pin
    (procedure in the workflow header), not automatically.

Usage:
  python3 scripts/check-kaniko-mirror.py [--workflow PATH] [--manifest PATH]

Exit codes:
  0  the inline Kaniko job matches the pinned mirror point
  1  drift detected, or the workflow/manifest is missing or malformed
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-image.yaml"
DEFAULT_MANIFEST = REPO_ROOT / ".github" / "kaniko-mirror.json"

# A Kaniko flag is a YAML list item whose scalar starts with `--`. In the
# workflow these lines live inside a `cat <<EOF | kubectl apply -f -` heredoc,
# one per line, indented.
FLAG_RE = re.compile(r"^\s+-\s+(--\S+)\s*$", re.MULTILINE)

# The heredoc interpolates shell variables at the end of the cache flag and the
# destination list; neither is part of the flag name.
GLUE_SUFFIX = "${DEST_ARGS}"


def normalise_flag(token: str) -> str:
    """Reduce a captured YAML list item to the Kaniko flag it represents."""
    token = token.rstrip("\"'")
    return token.removesuffix(GLUE_SUFFIX)


def extract_flags(text: str) -> list[str]:
    """Return every Kaniko flag in the workflow, in file order."""
    return [normalise_flag(match.group(1)) for match in FLAG_RE.finditer(text)]


def fail(message: str) -> int:
    print(f"kaniko-mirror: FAIL: {message}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=pathlib.Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    if not args.workflow.is_file():
        return fail(f"workflow not found: {args.workflow}")
    if not args.manifest.is_file():
        return fail(f"mirror manifest not found: {args.manifest}")

    workflow = args.workflow.read_text(encoding="utf-8")
    try:
        mirror = json.loads(args.manifest.read_text(encoding="utf-8"))["mirror"]
    except (json.JSONDecodeError, KeyError) as exc:
        return fail(f"{args.manifest} is malformed: {exc}")

    provenance = args.manifest.name
    problems: list[str] = []

    # 1. Provenance must be complete and well-formed, so nobody can "refresh"
    #    the pin by blanking it.
    for field in ("source_repository", "source_path", "source_ref", "source_commit", "source_blob"):
        if not str(mirror.get(field, "")).strip():
            problems.append(f"{provenance}: mirror.{field} is empty")
    commit = str(mirror.get("source_commit", ""))
    if commit and not re.fullmatch(r"[0-9a-f]{40}", commit):
        problems.append(f"{provenance}: mirror.source_commit is not a 40-hex commit: {commit!r}")
    blob = str(mirror.get("source_blob", ""))
    if blob and not re.fullmatch(r"[0-9a-f]{40}", blob):
        problems.append(f"{provenance}: mirror.source_blob is not a 40-hex blob id: {blob!r}")
    pinned_at = str(mirror.get("pinned_at", ""))
    try:
        datetime.date.fromisoformat(pinned_at)
    except ValueError:
        problems.append(f"{provenance}: mirror.pinned_at is not an ISO date: {pinned_at!r}")

    # 2. The human-readable pin in the workflow comment must name the same
    #    commit and file as the machine-readable manifest. Otherwise a reader
    #    following the header mirrors a different point than the guard enforces.
    if commit and commit not in workflow:
        problems.append(
            f"the workflow's provenance comment does not mention the pinned commit {commit}; "
            "update the header comment or the manifest so they agree"
        )
    source_path = str(mirror.get("source_path", ""))
    if source_path and source_path not in workflow:
        problems.append(
            f"the workflow's provenance comment does not mention the mirrored file {source_path!r}"
        )

    # 3. The builder image and the clone image are part of the mirror: pinning
    #    flags while silently bumping the executor would still build elsewhere.
    for field in ("kaniko_image", "clone_image"):
        image = str(mirror.get(field, ""))
        if not image:
            problems.append(f"{provenance}: mirror.{field} is empty")
        elif image not in workflow:
            problems.append(f"the workflow does not use the pinned {field} {image!r}")

    # 4. Job skeleton fields the reusable's contract depends on (service
    #    account, push secret, bounded Job).
    for needle in mirror.get("required_strings", []):
        if needle not in workflow:
            problems.append(f"the workflow no longer contains required Job field {needle!r}")

    # 5. The flags themselves. Compare as multisets so a duplicated flag is
    #    drift too, but report in a stable, diffable order.
    expected = [normalise_flag(flag) for flag in mirror.get("expected_flags", [])]
    if not expected:
        problems.append(f"{provenance}: mirror.expected_flags is empty")
    actual = extract_flags(workflow)

    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    for flag in missing:
        problems.append(f"flag missing from the workflow: {flag}")
    for flag in unexpected:
        problems.append(f"flag present in the workflow but not in the pinned mirror: {flag}")
    for flag in sorted(set(expected) & set(actual)):
        if expected.count(flag) != actual.count(flag):
            problems.append(
                f"flag {flag} appears {actual.count(flag)}x in the workflow but "
                f"{expected.count(flag)}x in the pinned mirror"
            )

    if problems:
        print("kaniko-mirror: the inlined Kaniko job has drifted from its pinned mirror point.", file=sys.stderr)
        print(
            f"kaniko-mirror: mirror = {mirror.get('source_repository')}@{commit} "
            f"{source_path} ({mirror.get('mirrored_job')}, {mirror.get('mirrored_path')})",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"kaniko-mirror:   - {problem}", file=sys.stderr)
        print(
            "kaniko-mirror: if the change is intended, refresh the pin in ONE commit: port the "
            "reusable's current job, update "
            f"{args.manifest.name} (source_commit/source_blob/pinned_at/expected_flags/images/"
            "required_strings) and the commit hash in publish-image.yaml's header. See the "
            "'Mirror provenance' block there.",
            file=sys.stderr,
        )
        return 1

    print(
        "kaniko-mirror: OK — the inlined Kaniko job matches the pin "
        f"{mirror['source_repository']}@{commit[:8]} {source_path} "
        f"({len(expected)} flags, {len(mirror.get('required_strings', []))} Job fields, "
        f"{mirror['kaniko_image']})."
    )
    print(
        "kaniko-mirror: note — this proves the inline copy is unchanged since the pin; it cannot "
        "detect upstream changes to the private reusable."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
