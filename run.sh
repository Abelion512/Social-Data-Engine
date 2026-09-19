#!/usr/bin/env bash
# TikTok → LinkedIn Pipeline Launcher (multi-provider: TikTok / LinkedIn)
#
# Compatibility:
#   bash 4+  and  zsh 5.8+
#   Run via:  bash run.sh          (recommended)
#      or:    source run.sh        (sourcing is also safe)
#
# Usage:
#   bash run.sh                                  # login mode (persistent browser profile)
#   bash run.sh "https://www.tiktok.com/@user/video/123" --max 100
#   bash run.sh "https://..." --connect          # auto-connect (requires approval)
#
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Activate the project venv when it exists; otherwise fall back to the system
# interpreter with an actionable hint instead of dying on a missing activate
# script (previously: `source .venv/bin/activate` hard-failed with no context).
PY="python"
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
else
    echo "⚠️  .venv/bin/activate tidak ditemukan — memakai interpreter sistem." >&2
    echo "    Setup: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
    if command -v python3 >/dev/null 2>&1; then
        PY="python3"
    fi
fi

# ---------------------------------------------------------------------------
# load_env <envfile> <whitelist...>  — zsh + bash safe credential sourcing
#
# The old idiom `export $(grep ... | xargs)` is a bash-ism: zsh does NOT
# word-split command substitution by default, so `export $(...)` misbehaves
# (only the first KEY=VALUE survives, values with spaces break).  This line-
# reader parses KEY=VALUE pairs explicitly and works identically under bash,
# zsh, and dash.
# ---------------------------------------------------------------------------
load_env() {
    local envfile="$1"
    shift
    [ -f "$envfile" ] || return 0

    # Build a pipe-separated whitelist: |KEY1|KEY2|...
    local whitelist="|$(printf '%s|' "$@")|"
    local line key val

    while IFS= read -r line || [ -n "$line" ]; do
        # strip inline comments and surrounding whitespace
        line="${line%%#*}"
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [ -z "$line" ] && continue

        # extract key (before first '=')
        key="${line%%=*}"
        [ -z "$key" ] && continue

        # only export whitelisted keys (zsh-safe: no for-loop local var quirk)
        case "$whitelist" in
            *"|$key|"*) : ;;  # matched — continue to export below
            *) continue ;;    # not whitelisted
        esac

        # extract value (after first '=') and strip matching quotes
        val="${line#*=}"
        case "$val" in
            \"*\") val="${val#\"}"; val="${val%\"}" ;;
            \'*\') val="${val#\'}"; val="${val%\'}" ;;
        esac
        export "$key=$val"
    done < "$envfile"
}

# Source 9Router API key + vision config. load_env only exports whitelisted
# KEY=VALUE lines (zsh/bash safe) — explicitly skips the multiline SSH-key and
# other non-credential blobs that break naive `source`.
load_env "$HOME/.hermes/.env" \
    "NINEROUTER_API_KEY" \
    "NINEROUTER_URL" \
    "MODEL_ID" \
    "MODEL_VISION_DEFAULT" \
    "MODEL_VISION_OCR"

# NOTE: LinkedIn / TikTok auth is COOKIE-BASED, never password-based.
# The user logs in manually once (run.sh, no URL → login mode) into a
# persistent Chrome/Camoufox profile at ~/.tiktok-linkedin/chrome-profile/;
# subsequent automation reuses the saved session cookies. No username or
# password is stored or referenced in the codebase. Therefore we do NOT
# load LINKEDIN_USERNAME / LINKEDIN_PASSWORD here (previously a leftover
# that invited secret leakage).

if [ -z "${1:-}" ]; then
    # No URL → login mode
    "$PY" src/tiktok_linkedin.py --login
else
    # URL provided → run pipeline
    mkdir -p "$DIR/logs"
    LOG="$DIR/logs/pipeline_$(date +%Y%m%d_%H%M%S).log"
    "$PY" src/tiktok_linkedin.py "$@" 2>&1 | tee "$LOG"
    echo ""
    echo "Results: ~/.tiktok-linkedin/state/"
    echo "Log: $LOG"
fi
