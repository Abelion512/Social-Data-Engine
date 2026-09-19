#!/usr/bin/env bash
# test_live.sh — pre-flight check sebelum test live CDP (bash/zsh).
# Pastikan .venv, dependencies, dan setidaknya satu browser CDP (9222-9236)
# sudah berjalan, sebelum lanjut ke collector.
#
# Ini HANYA pre-flight — live collection-nya sendiri:
#   bash run.sh "https://www.tiktok.com/@user/video/ID" --max 50 --scrolls 40
#
#   Usage:  ./scripts/test_live.sh
#           PYTHON=.venv/bin/python ./scripts/test_live.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
[ -x "$PYTHON" ] || { echo "❌ .venv tidak ada — jalankan dulu: python -m venv .venv"; exit 1; }

echo "### [1/4] Syntax & import ###"
"$PYTHON" -m compileall -q src/ scripts/
"$PYTHON" -c "import importlib,sys; sys.path.insert(0,'.'); \
[importlib.import_module(m) for m in ['src.browser_selector','src.harness.agent']]; \
print('core imports OK')"
# camoufox_collector butuh package `camoufox` (opsional — jalur CDP tidak pakai itu).
"$PYTHON" -c "import importlib,sys; sys.path.insert(0,'.'); \
importlib.import_module('scripts.camoufox_collector')" 2>/dev/null \
  || echo "  (skip scripts.camoufox_collector — camoufox tidak terpasang; jalur CDP tetap jalan; opsional: $PYTHON -m pip install camoufox)"

echo
echo "### [2/4] Pyflakes (harus 0 fatal) ###"
if "$PYTHON" -c "import pyflakes" >/dev/null 2>&1; then
    FATAL=$("$PYTHON" -m pyflakes src/ scripts/ 2>&1 | grep -cE "undefined name|cannot be resolved|No module|assigned to but never" || true)
    echo "fatal errors: $FATAL"
    if [ "$FATAL" -gt 0 ]; then
        echo "❌ ada fatal — perbaiki dulu"
        exit 1
    fi
else
    # Opsional, bukan pemblokir: skrip tetap lanjut tanpa pyflakes.
    echo "  (skip — pyflakes tidak terpasang; opsional: $PYTHON -m pip install pyflakes)"
fi

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
echo "✅ Pre-flight OK — lanjut ke live collection, mis.:"
echo "    bash run.sh 'https://www.tiktok.com/@user/video/ID' --max 50 --scrolls 40"
echo "  atau module path langsung:"
echo "    TIKTOK_COOKIES=~/path/to/cookies.json $PYTHON -m src.collector 'https://tiktok.com/@user/video/ID'"
