#!/usr/bin/env bash
# check_deps.sh — scan & audit dependencies (bash/zsh compatible, Linux/macOS)
#
# 1) Outdated packages
# 2) Vulnerability scan via pip-audit
# 3) Dependency tree (pipdeptree, opsional)
#
# Usage:  ./scripts/check_deps.sh
# Env:    PYTHON=/path/to/python (default .venv/bin/python)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3 || command -v python)"

echo "### [1/3] Outdated packages ###"
"$PYTHON" -m pip list --outdated 2>/dev/null | sed -n '1,40p' || true

echo
echo "### [2/3] Security audit (pip-audit) ###"
if command -v pip-audit >/dev/null 2>&1; then
    PIP_AUDIT=pip-audit
elif [ -x ".venv/bin/pip-audit" ]; then
    PIP_AUDIT=".venv/bin/pip-audit"
else
    echo "pip-audit belum terpasang. Install dulu:"
    echo "  $PYTHON -m pip install pip-audit"
    PIP_AUDIT=""
fi
if [ -n "$PIP_AUDIT" ]; then
    "$PIP_AUDIT" 2>/dev/null || "$PIP_AUDIT" --no-deps
fi

echo
echo "### [3/3] Dependency tree (opsional) ###"
if command -v pipdeptree >/dev/null 2>&1; then
    pipdeptree --warn fail 2>/dev/null | sed -n '1,40p' || true
else
    echo "pipdeptree belum terpasang (opsional): $PYTHON -m pip install pipdeptree"
fi

echo
echo "✅ deps check selesai."
