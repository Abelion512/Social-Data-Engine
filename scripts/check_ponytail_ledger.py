#!/usr/bin/env python3
"""
Ponytail-ceiling gate — every inline `ponytail:` marker in src/ + scripts/
must have a matching entry in docs/PONYTAIL.md (the debt ledger).

Why: the inline marker documents *local* intent (ceiling + upgrade path);
the ledger is the *repo-wide* debt inventory an agent reads before it
"improves" a file. A marker without a ledger row means the next contributor
sees half the contract — this gate makes that impossible to merge.

Parse contract (kept deliberately simple):
  marker   → `ponytail:` anywhere on the line (case-sensitive, like CI step 6)
  ledger   → any line in docs/PONYTAIL.md whose **first cell of a markdown
             table row** matches `| <file-ish>` (absolute or relative).

Both sides are compared on the *basename* of the file the marker lives in, so
`# ponytail: ...` in src/pipeline/legacy.py is satisfied by either
`| src/pipeline/legacy.py |` or `| legacy.py | ...` in the ledger.

Run:  python scripts/check_ponytail_ledger.py   (CI pre-merge gate #6)
Exit: 0 if every marker is covered; 1 with a fix-me list otherwise.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("src", "scripts")
LEDGER = ROOT / "docs" / "PONYTAIL.md"
MARKER = "ponytail:"          # same convention as the CI deferred-work gate (CI step 6)
SELF = Path(__file__).name    # this checker quotes the convention — exclude itself


def marker_files() -> dict:
    """{file basename → {line numbers with a ponytail: marker}}."""
    found = {}
    for d in SCAN_DIRS:
        for f in sorted((ROOT / d).rglob("*.py")):
            if f.name == SELF:
                continue
            hits = [i for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
                    if MARKER in line]
            if hits:
                found[str(f.relative_to(ROOT))] = hits
    return found


def ledger_files() -> set:
    """Basenames referenced anywhere in PONYTAIL.md.

    The §6 ledger row format is `| <ceiling text> | <where: src/foo.py> | <revisit when> |`,
    so the file may sit in ANY cell — we therefore scan the whole line for
    repo paths (`src/foo.py`, `scripts/bar.py`, incl. `foo.py::func` suffixes)
    and additionally accept a file-ish first cell (`| src/foo.py | ... |`).
    """
    if not LEDGER.exists():
        return set()
    names = set()
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        for m in re.finditer(r"(?:src|scripts|docs)/[\w./-]+\.py", s):
            names.add(Path(m.group(0)).name)
        m = re.match(r"^\|\s*([^|]+)\|", s)          # | <file> | ... |
        if m:
            cell = m.group(1).strip().strip("`")
            if cell.endswith(".py"):
                names.add(Path(cell).name)
    return names


def main() -> int:
    marked = marker_files()
    ledgered = ledger_files()
    if not LEDGER.exists():
        print(f"[ponytail-gate] ❌ {LEDGER.relative_to(ROOT)} missing — create the debt ledger first")
        return 1

    uncovered = {f: ls for f, ls in marked.items()
                 if Path(f).name not in ledgered}
    print(f"[ponytail-gate] {sum(len(v) for v in marked.values())} marker(s) in "
          f"{len(marked)} file(s) · {len(ledgered)} ledger row(s) in docs/PONYTAIL.md")

    if uncovered:
        print("[ponytail-gate] ❌ ponytail: markers with no debt-ledger entry — add a row to docs/PONYTAIL.md:")
        for f in sorted(uncovered):
            print(f"  - {f} (line{'s' if len(uncovered[f]) > 1 else ''} "
                  f"{', '.join(map(str, uncovered[f]))})")
        print("\nLedger row format (§6): `| <ceiling text> | src/<file>.py | <revisit when> |`")
        return 1
    print("[ponytail-gate] ✅ every inline ponytail: marker has a docs/PONYTAIL.md debt entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
