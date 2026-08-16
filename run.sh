#!/bin/bash
# TikTok → LinkedIn Pipeline Launcher
# Usage: bash run.sh [tiktok_url] [--connect]

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
source .venv/bin/activate

# Source 9Router API key
[ -f "$HOME/.hermes/.env" ] && export $(grep -E '^NINEROUTER_API_KEY=' "$HOME/.hermes/.env" | xargs) 2>/dev/null

# Source LinkedIn creds (optional; fill ~/.tiktok-linkedin/.env if using auto-login)
[ -f "$HOME/.tiktok-linkedin/.env" ] && export $(grep -E '^(LINKEDIN_USERNAME|LINKEDIN_PASSWORD)=' "$HOME/.tiktok-linkedin/.env" | xargs) 2>/dev/null

if [ -z "$1" ]; then
    # No URL → login mode
    python tiktok_linkedin.py --login
else
    # URL provided → run pipeline
    LOG="$DIR/pipeline_$(date +%Y%m%d_%H%M%S).log"
    python tiktok_linkedin.py "$@" 2>&1 | tee "$LOG"
    echo ""
    echo "Results: ~/.tiktok-linkedin/state/"
    echo "Log: $LOG"
fi
