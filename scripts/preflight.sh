#!/usr/bin/env bash
# preflight — every pre-merge gate in one command (agents + humans; CI = same 8).
# Usage:  bash scripts/preflight.sh            (python3 default)
#         bash scripts/preflight.sh .venv/bin/python
#
# Exit 0 = all gates green, exit 1 = at least one gate failed.
# NOTE: the bracket trick below (T[O]DO, LINKEDIN_PASSWOR[D]) keeps this file
# from self-matching gates 5/6 — the regexes still match the real targets.
set -uo pipefail
PY="${1:-python3}"
fail=0
say() { printf '\n== %s ==\n' "$1"; }

say "1/8 compile all .py"
"$PY" -m py_compile src/*.py src/*/*.py scripts/*.py tests/*.py || fail=1

say "2/8 import + symbol cross-check"
"$PY" - <<'EOF' || fail=1
import importlib
checks = {
  "src.export": {"manifest", "mark"},
  "src.export.manifest": ["build_manifest", "write_manifest"],
  "src.export.mark": ["export_video", "export_all", "_to_mark_format"],
  "src.pipeline.identity": ["resolve_identity"],
  "src.pipeline.stages": ["StageRunner"],
  "src.pipeline.improve": ["PipelineMetrics", "ImprovementPlan", "Action",
                            "ImprovementPlanner", "SelfHealingPipeline"],
}
ok = True
for mod, syms in checks.items():
    m = importlib.import_module(mod)
    miss = [s for s in syms if not hasattr(m, s)]
    if miss:
        ok = False
        print(f"FAIL {mod} missing {miss}")
if not ok:
    raise SystemExit("symbol check failed")
print("all symbols present")
EOF

say "3/8 run.sh shell syntax (bash + zsh when present)"
bash -n run.sh || fail=1
if command -v zsh >/dev/null 2>&1; then
  zsh -n run.sh || fail=1
else
  echo "  (zsh not installed locally — skipped; CI enforces it)"
fi

say "4/8 deterministic unit suites"
found=0
for suite in tests/test_*.py tests/run_*_tests.py; do
  [ -e "$suite" ] || continue
  found=$((found + 1))
  if "$PY" "$suite" >/dev/null 2>&1; then
    echo "  ok   $suite"
  else
    echo "  FAIL $suite — rerun: $PY $suite"
    fail=1
  fi
done
if [ "$found" -eq 0 ]; then
  echo "  no test suites discovered under tests/"
  fail=1
fi
echo "  $found suite(s) run"

say "5/8 security — no plaintext credentials"
if grep -rnI 'LINKEDIN_PASSWOR[D]\|LINKEDIN_USERNAM[E]' \
     --exclude-dir=__pycache__ --exclude-dir=.pytest_cache \
     src/ tests/ scripts/; then
  echo "  FAIL plaintext credential name found in source"
  fail=1
else
  echo "  ok — no plaintext credential names in code"
fi

say "6/8 deferred-work markers in src/ scripts/"
if grep -rnIE '\b(T[O]DO|FIXM[E]|XX[X]|HAC[K])\b' src/ scripts/ --exclude-dir=__pycache__; then
  echo "  FAIL finish it, or record a 'ponytail:' ceiling + docs/PONYTAIL.md debt entry"
  fail=1
else
  echo "  ok — no deferred-work markers"
fi

say "7/8 ponytail ledger — every inline ceiling marker has a debt entry"
"$PY" scripts/check_ponytail_ledger.py || fail=1

say "8/8 dependency gate — no undeclared third-party imports"
"$PY" scripts/check_dependencies.py || fail=1

printf '\n'
if [ "$fail" -eq 0 ]; then
  echo "PREFLIGHT: all 8 gates green"
else
  echo "PREFLIGHT: FAILED — fix the gates above before merge (agents.md checklist)"
fi
exit "$fail"
