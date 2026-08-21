#!/usr/bin/env python3
"""
version_bump.py — deterministic, stdlib-only semver bumper (major|minor|patch).

Authoritative version lives in `src/schema/mapper.py :: PIPELINE_VERSION = "X.Y.Z"`.
This tool bumps it (plus `COLLECTOR_VERSION` + `SCHEMA_VERSION` in
`src/tiktok_schema.py` = `X.Y`) and optionally commits + tags + pushes.

The 9Router / LLM model versions are NEVER touched by this tool — only the two
canonical data-layer version constants are bumped.

Usage (ponytail-ladder: dry-run by default, no surprising writes):
    python scripts/version_bump.py --bump minor              # → print, no write
    python scripts/version_bump.py --bump patch --commit     # write + commit + tag
    python scripts/version_bump.py --bump minor --commit --push   # + push tag
"""
from __future__ import annotations
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAPPER = ROOT / "src" / "schema" / "mapper.py"
TAKTOK = ROOT / "src" / "tiktok_schema.py"


def _read_current() -> tuple[int, int, int]:
    t = MAPPER.read_text(encoding="utf-8")
    m = re.search(r'PIPELINE_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', t)
    if not m:
        raise SystemExit("ERROR: cannot parse PIPELINE_VERSION in mapper.py")
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _bump(major: int, minor: int, patch: int, part: str) -> tuple[int, int, int]:
    if part == "major":
        return major + 1, 0, 0
    if part == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def _write_new(maj: int, mn: int, pt: int) -> str:
    new_ver = f"{maj}.{mn}.{pt}"
    new_schema = f"{maj}.{mn}"          # SCHEMA_VERSION stays X.Y (data layer)
    new_coll = new_ver                  # COLLECTOR_VERSION mirrors pipeline

    # mapper.py — PIPELINE_VERSION
    txt = MAPPER.read_text(encoding="utf-8")
    txt = re.sub(r'PIPELINE_VERSION\s*=\s*"[^"]+"',
                 f'PIPELINE_VERSION = "{new_ver}"', txt, count=1)
    MAPPER.write_text(txt, encoding="utf-8")

    # tiktok_schema.py — COLLECTOR_VERSION + SCHEMA_VERSION
    s = TAKTOK.read_text(encoding="utf-8")
    s = re.sub(r'SCHEMA_VERSION\s*=\s*"[^"]+"',
               f'SCHEMA_VERSION = "{new_schema}"', s, count=1)
    s = re.sub(r'COLLECTOR_VERSION\s*=\s*"[^"]+"',
               f'COLLECTOR_VERSION = "{new_coll}"', s, count=1)
    TAKTOK.write_text(s, encoding="utf-8")
    return new_ver


def _git(*args: str) -> None:
    subprocess.run(["git", "-C", str(ROOT), *args], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Bump semver version constants.")
    ap.add_argument("--bump", required=True, choices=["major", "minor", "patch"])
    ap.add_argument("--commit", action="store_true",
                    help="write files, commit, and tag vX.Y.Z")
    ap.add_argument("--push", action="store_true",
                    help="push commit + tag (requires --commit)")
    args = ap.parse_args()

    if args.push and not args.commit:
        ap.error("--push requires --commit")

    major, minor, patch = _read_current()
    nmaj, nmin, npt = _bump(major, minor, patch, args.bump)
    new_ver = f"{nmaj}.{nmin}.{npt}"

    print(f"  current : {major}.{minor}.{patch}")
    print(f"  bump    : {args.bump}")
    print(f"  new     : {new_ver}")

    if not args.commit:
        print("  dry-run  : pass --commit --push to apply  (ponytail: no side-effects yet)")
        return 0

    _write_new(nmaj, nmin, npt)
    _git("add", "src/schema/mapper.py", "src/tiktok_schema.py")
    _git("commit", "-m", f"chore: bump version to v{new_ver} ({args.bump})")
    _git("tag", f"v{new_ver}")
    print(f"  tagged  : v{new_ver}")
    if args.push:
        # Safety: NEVER push all local branches (could push stray local work to
        # main!). Push ONLY the currently checked-out branch + the new tag.
        branch = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
        ).strip()
        _git("push", "origin", branch)
        _git("push", "origin", f"v{new_ver}")
        print(f"  pushed  : {branch} + tag v{new_ver}")
    print(f"✅ version bumped {major}.{minor}.{patch} → {new_ver}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
