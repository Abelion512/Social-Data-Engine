#!/usr/bin/env bash
# install.sh — copy the Social Data Engine plugin into a host's plugin folder.
#
# Host-agnostic: this script knows no specific agent. `--dir` is whatever folder
# your host loads plugins from. It copies `manifest.json` + `index.js` and writes
# `plugin.runtime.json` (absolute repo root + interpreter) so the adapter never
# guesses where the engine lives.
#
# Usage:
#   bash integrations/plugin/install.sh --dir ~/some-host/plugins/sde-social-data
#   bash integrations/plugin/install.sh --dir ./dist/plugin --python /usr/bin/python3
#   bash integrations/plugin/install.sh --dir ./dist/plugin --dry-run
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"

TARGET_DIR=""
PYTHON=""
DRY_RUN=0

usage() {
  cat <<'USAGE'
install.sh — install the Social Data Engine plugin into a host plugin folder

  --dir DIR          target plugin folder (required)
  --python PATH      interpreter for the bridge (default: <repo>/.venv/bin/python, else python3)
  --dry-run          print the plan, write nothing
  -h, --help         this text
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dir) TARGET_DIR="${2:-}"; shift 2 ;;
    --python) PYTHON="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$TARGET_DIR" ]; then
  echo "❌ --dir wajib (folder plugin host kamu)" >&2
  usage >&2
  exit 2
fi

# ── reject values that cannot be represented safely in the JSON config ────────
case "$TARGET_DIR$REPO_ROOT" in
  *'
'*) echo "❌ path mengandung newline — ditolak" >&2; exit 1 ;;
esac

# ── resolve interpreter (validate it can actually import the engine) ──────────
if [ -z "$PYTHON" ] && [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
fi
if [ -z "$PYTHON" ]; then
  PYTHON="$(command -v python3 || true)"
fi
if [ -z "$PYTHON" ]; then
  echo "❌ tidak menemukan interpreter Python — pakai --python PATH" >&2
  exit 1
fi
if ! (cd "$REPO_ROOT" && "$PYTHON" -c 'import src.mcp_server' >/dev/null 2>&1); then
  echo "❌ '$PYTHON' tidak bisa mengimpor src.mcp_server dari $REPO_ROOT" >&2
  echo "   Install dulu: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi

echo "repo root   : $REPO_ROOT"
echo "interpreter : $PYTHON"
echo "plugin dir  : $TARGET_DIR"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "(dry-run) would copy manifest.json + index.js and write plugin.runtime.json — nothing written"
  exit 0
fi

mkdir -p "$TARGET_DIR"
cp "$SRC_DIR/manifest.json" "$TARGET_DIR/manifest.json"
cp "$SRC_DIR/index.js" "$TARGET_DIR/index.js"

# The config carries two filesystem paths, so it is written by the interpreter
# we just validated instead of by string interpolation: `json.dump` escapes
# quotes/backslashes correctly, and a path containing `"` (or `\`) can therefore
# not corrupt the file or inject extra keys into it.
PYTHON="$PYTHON" REPO_ROOT="$REPO_ROOT" TARGET_DIR="$TARGET_DIR" "$PYTHON" - <<'PY'
import json, os, pathlib
payload = {
    "repo_root": os.environ["REPO_ROOT"],
    "python": os.environ["PYTHON"],
    "plugin_version": "1.0.0",
}
out = pathlib.Path(os.environ["TARGET_DIR"]) / "plugin.runtime.json"
out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo
echo "✅ plugin terpasang: $TARGET_DIR"
echo "   cek cepat: node $TARGET_DIR/index.js --list-tools"
echo
echo "Kalau host kamu memakai konvensi plugin sendiri (mis. memerlukan berkas"
echo "manifest dengan daftar aksi), bungkus adapter ini — jangan salin logikanya:"
echo "  docs/INTEGRATIONS/PLUGIN.md §host-specific adapter (12 baris)"
