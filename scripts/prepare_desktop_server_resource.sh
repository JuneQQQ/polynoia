#!/usr/bin/env bash
set -euo pipefail

# Prepare a lightweight backend resource for the Tauri desktop bundle.
# Do not copy apps/server/.venv; the desktop runtime runs the resource with uv
# and keeps its per-app environment under the user's app data directory.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="$REPO/apps/server"
DST="$REPO/apps/desktop/src-tauri/resources/server"

rm -rf "$DST"
mkdir -p "$DST"

rsync -a \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude ".ruff_cache" \
  --exclude "dist" \
  --exclude "*.db" \
  --exclude "*.db-shm" \
  --exclude "*.db-wal" \
  "$SRC/pyproject.toml" \
  "$SRC/uv.lock" \
  "$SRC/polynoia" \
  "$DST/"

echo "Prepared desktop backend resource: $DST"
