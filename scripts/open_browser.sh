#!/usr/bin/env bash
# open_browser.sh — launch a Chromium-family browser WITH CDP flag (zsh/bash-safe).
#
# FIX zsh: '*' di --remote-allow-origins HARUS di-quote agar tidak diekspansi glob
#  (zsh: "no matches found: --remote-allow-origins=*"). Assignment single-quoted
#  → expansion aman.
#
# FIX google-chrome tidak ada di mesin user: auto-detect brave/chromium/edge.
#
# Brave **harus** dilaunch dengan flag ini agar CDP terbuka — attach saja ke
# instance tanpa --remote-debugging-port tidak cukup (scan CDP akan kosong).
#
# Usage:
#   ./scripts/open_browser.sh                 # detect + launch on :9222
#   PORT=9223 PROFILE=/path ./scripts/open_browser.sh
#   ./scripts/open_browser.sh detect           # cek browser apa saja tersedia
set -euo pipefail

PORT="${PORT:-9222}"
# reuse profil kita (kosongkan bila mau profil default browser)
PROFILE="${PROFILE:-$HOME/.tiktok-linkedin/chrome-profile}"
# single-quoted '*' → zombie-proof against zsh glob expansion
ALLOW='*'

if [ "${1:-}" = "detect" ]; then
  echo "🔍 chromium-family browser tersedia:"
  for c in brave-browser google-chrome-stable google-chrome chromium microsoft-edge chromium-browser; do
    command -v "$c" >/dev/null 2>&1 && echo "  ✅ $c -> $(command -v "$c")"
  done
  command -v google-chrome >/dev/null 2>&1 && echo "  ✅ google-chrome -> $(command -v google-chrome)"
  exit 0
fi

pick=""
for c in brave-browser google-chrome-stable google-chrome chromium microsoft-edge chromium-browser; do
  if command -v "$c" >/dev/null 2>&1; then pick="$c"; break; fi
done

if [ -z "$pick" ]; then
  echo "❌ tidak ada chromium-family browser (brave/chrome/edge/chromium) di PATH."
  echo "   Install dulu, mis: sudo apt install brave-browser"
  exit 1
fi

echo "▶ Membuka: $pick --remote-debugging-port=$PORT --remote-allow-origins='*' --user-data-dir=$PROFILE"
echo "   ⚠️ flag ini WAJIB agar scan CDP detect browsermu (bukan attach ke instance tanpa flag)."
exec "$pick" \
  --remote-debugging-port="$PORT" \
  --remote-allow-origins="$ALLOW" \
  --user-data-dir="$PROFILE" \
  --remote-allow-credentials
