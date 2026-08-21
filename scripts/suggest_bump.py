#!/usr/bin/env python3
"""
suggest_bump.py — deterministic semver bump suggester (stdlib-only).

Answers: "which part should I bump?" by classifying conventional commits
since the last tag:

    BREAKING CHANGE (footer) or `type!:`  →  major
    feat: ...                             →  minor
    fix: ... / perf: ...                  →  patch
    anything else (chore/docs/test/ci/…)  →  (no signal; patch floor)

The suggested bump is the HIGHEST signal present. If no commits carry a
recognised type, it falls back to "patch" (safest non-breaking floor) and
says so. This is a SUGGESTION only — the live-test gate in agents.md and
the semver table in docs/VERSIONING.md still govern the real bump.

Usage:
    python scripts/suggest_bump.py                 # text report
    python scripts/suggest_bump.py --json          # machine-readable
    python scripts/suggest_bump.py --from v1.0.0   # override base ref
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MAJOR_TYPES = set()          # breaking is signalled via `!` or footer, not a type
MINOR_TYPES = {"feat"}
PATCH_TYPES = {"fix", "perf"}
OTHER_TYPES = {"chore", "docs", "test", "ci", "style", "refactor", "build"}

SUBJECT_RE = re.compile(r"^(\w+)(?:\(([^)]*)\))?(!)?:\s+(.+)$")
BREAKING_FOOTER_RE = re.compile(r"^BREAKING[- ]CHANGE:", re.MULTILINE)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _base_ref(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    r = subprocess.run(
        ["git", "-C", str(ROOT), "describe", "--tags", "--abbrev=0"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return r.stdout.strip()
    # No tags yet: fall back to the first commit (or None on an empty repo).
    r = subprocess.run(
        ["git", "-C", str(ROOT), "rev-list", "--max-parents=0", "HEAD"],
        capture_output=True, text=True,
    )
    return r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else None


def classify(subject: str, body: str) -> str:
    m = SUBJECT_RE.match(subject)
    if not m:
        return "other"
    kind, _, bang, _ = m.groups()
    if bang or BREAKING_FOOTER_RE.search(body):
        return "major"
    if kind in MINOR_TYPES:
        return "minor"
    if kind in PATCH_TYPES:
        return "patch"
    return "other"


def collect(base: str | None) -> list[dict]:
    rng = f"{base}..HEAD" if base else "HEAD"
    # NOTE: first field is %h so a leading \x1f separator can never be eaten
    # by whitespace-stripping (\x1f is Unicode whitespace).
    out = _git("log", "--format=%h%x1f%s%x1f%b%x1e", rng)
    commits = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        parts = chunk.split("\x1f")
        subject = parts[1].strip() if len(parts) > 2 else ""
        body = parts[2].strip() if len(parts) > 2 else ""
        if not subject:
            continue
        commits.append({
            "subject": subject,
            "bump": classify(subject, body),
        })
    return commits


def main() -> int:
    ap = argparse.ArgumentParser(description="Suggest semver bump from commits since last tag.")
    ap.add_argument("--from", dest="base", default=None, help="override base tag/ref")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    base = _base_ref(args.base)
    commits = collect(base)

    signals = [c["bump"] for c in commits]
    if "major" in signals:
        suggested = "major"
    elif "minor" in signals:
        suggested = "minor"
    else:
        suggested = "patch"  # floor: fix commits, chores, or unrecognised subjects

    if args.json:
        print(json.dumps({
            "base": base, "suggested_bump": suggested,
            "commits": commits,
        }, indent=2))
        return 0

    print(f"  base      : {base or '(no commits / empty repo)'}")
    print(f"  commits   : {len(commits)}")
    print()
    counts = {"major": 0, "minor": 0, "patch": 0, "other": 0}
    for c in commits:
        counts[c["bump"]] += 1
        print(f"  [{c['bump']:>5}] {c['subject']}")
    print()
    print(f"  signals   : major={counts['major']}  minor={counts['minor']}  "
          f"patch={counts['patch']}  other={counts['other']}")

    if not commits:
        print("  suggested : (nothing to release)")
        return 0
    if counts == {"major": 0, "minor": 0, "patch": 0, "other": len(commits)}:
        print("  note      : no conventional-commit types found — patch floor applied")
    print(f"  suggested : --bump {suggested}")
    print()
    print("  apply     : python scripts/version_bump.py --bump "
          f"{suggested} --commit --push   (after live-test gate ✅)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
