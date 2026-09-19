#!/usr/bin/env python3
"""
Third-party dependency gate — import gate for CI (stdlib + requirements only).

Rule: a PR may import (a) the stdlib, (b) modules of this repo, or (c) a
third-party package declared in requirements.txt — either as an active line
(`pkg>=1.0`) or as a documented commented optional (`#   pkg>=0.38  → consumer`).
Anything else fails with the exact import that is missing, so the fix is either
"declare it in requirements.txt" (deliberate, reviewable dependency) or "don't
import it" (accidental undeclared dependency → blocks the merge).

Implementation detail: **AST only — nothing is imported, nothing executes.**
Top-level `import X` / `from X import ...` statements are classified statically;
no `importlib` probing, so scanning is safe even for modules with heavy optional
imports at module top level (those must be declared as commented optionals).

Run:  python scripts/check_dependencies.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ_FILE = ROOT / "requirements.txt"
SCAN_DIRS = ("src", "scripts", "plugins")  # plugins are code too — same gate
SELF_PREFIX = ("src", "scripts")

# stdlib module names (3.10+) — statically listed, no interpreter probing
try:
    STDLIB = set(sys.stdlib_module_names)
except AttributeError:  # <3.10 fallback: curated common list
    STDLIB = set("""asyncio json re sys os time random math hashlib pathlib
    typing collections datetime itertools functools urllib dataclasses enum
    argparse logging uuid bisect contextlib csv glob io subprocess shutil
    tempfile textwrap traceback unittest warnings weakref abc importlib
    inspect string struct threading queue socket ssl zlib base64 binascii
    unicodedata difflib gc platform signal site stat statistics""".split())

# Import-name → distribution-name spellings that differ (the common ones).
IMPORT_TO_DIST = {
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "bs4": "beautifulsoup4",
    "pil": "pillow",
}


def requirement_names() -> set:
    """Distribution names declared in requirements.txt.

    Parses both *active* lines (`pkg>=1.0`) and *documented optional* lines
    (commented, e.g. `#   nodriver>=0.38      → scripts/export_....py`).
    A commented option is a deliberate, reviewable declaration — moving a
    package into that list is exactly the review step this gate wants.
    """
    names = set()
    if not REQ_FILE.exists():
        return names
    for line in REQ_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            # commented optional: `#   name>=ver → consumer`
            m = re.match(r"#\s*([A-Za-z0-9_.-]+)\s*[<>=!~]", line)
            if m:
                names.add(m.group(1).lower())
            continue
        name = re.split(r"[<>=!~;\[ ]", line, 1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def normalize(dist_name: str) -> str:
    """PEP 503 normalization so `requirements.txt` spelling matches import spelling."""
    return re.sub(r"[-_.]+", "-", dist_name).lower()


def top_level_imports(path: Path) -> set:
    """Top-level module names of every import statement in one file (AST)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return set()  # py_compile gate reports syntax errors
    tops = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                tops.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.add(node.module.split(".")[0])
    return tops


def main() -> int:
    declared = {normalize(n) for n in requirement_names()}
    violations = []
    checked = 0

    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for f in sorted(base.rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            checked += 1
            for top in top_level_imports(f):
                if top in STDLIB:
                    continue
                if top.split(".")[0] in SELF_PREFIX:
                    continue
                dist = normalize(IMPORT_TO_DIST.get(top.lower(), top))
                if dist not in declared:
                    violations.append(f"{f.relative_to(ROOT)}: import {top}"
                                      f" → not declared in requirements.txt")

    print(f"[deps-gate] {checked} files scanned · {len(declared)} third-party "
          f"package(s) declared in requirements.txt (incl. commented optionals)")
    if violations:
        print("[deps-gate] ❌ imports not covered by requirements.txt:")
        for v in sorted(set(violations)):
            print(f"  - {v}")
        print("\nFix: add the package to requirements.txt (active line, or a")
        print("commented optional `#   name>=ver → consumer` for install-on-demand")
        print("tools). Undeclared third-party imports block the merge.")
        return 1
    print("[deps-gate] ✅ every third-party import is declared in requirements.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
