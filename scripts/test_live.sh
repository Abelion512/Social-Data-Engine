#!/usr/bin/env bash
# test_live.sh — pre-flight check sebelum test live CDP (bash/zsh).
# Pastikan .venv, dependencies, dan setidaknya satu browser CDP (9222-9236)
# sudah berjalan, sebelum lanjut ke collector.
#
#   Usage:  ./scripts/test_live.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
[ -x "$PYTHON" ] || { echo "❌ .venv tidak ada — jalankan dulu: python -m venv .venv"; exit 1; }

echo "### [1/4] Syntax & import ###"
"$PYTHON" -m compileall -q src/ scripts/
"$PYTHON" -c "import importlib,sys; sys.path.insert(0,'.'); \
[importlib.import_module(m) for m in ['src.browser_selector','src.harness.agent','scripts.camoufox_collector']]; \
print('imports OK')"

echo
echo "### [2/4] Pyflakes (harus 0 fatal) ###"
FATAL=$("$PYTHON" -m pyflakes src/ scripts/ 2>&1 | grep -cE "undefined name|cannot be resolved|No module|assigned to but never" || true)
echo "fatal errors: $FATAL"
[ "$FATAL" -gt 0 ] && { echo "❌ ada fatal — perbaiki dulu"; exit 1; }

echo
echo "### [3/4] Scan browser CDP (9222-9236) ###"
"$PYTHON" - <<'EOF'
import asyncio, sys
sys.path.insert(0,'.')
from src.browser_selector import detect_browsers
async def _s():
    bs = detect_browsers()
    if not bs:
        print("[scan] ❌ tidak ada browser CDP — start browser dengan --remote-debugging-port=9222")
        sys.exit(1)
    for b in bs:
        print(f"[scan] ✅ {b.browser_type} on port {b.port} ({b.title})")
asyncio.run(_s())
EOF

echo
echo "### [4/4] Login status TikTok (async) ###"
"$PYTHON" - <<'EOF'
import asyncio, sys
sys.path.insert(0,'.')
from src.browser_selector import _scan_with_login
async def _s():
    await _scan_with_login()
asyncio.run(_s())
EOF

echo
echo "✅ Pre-flight OK — lanjut ke collector, mis.:"
echo "    TIKTOK_COOKIES=~/path/to/cookies.json $PYTHON -m collector 'https://tiktok.com/@user/video/ID'"
