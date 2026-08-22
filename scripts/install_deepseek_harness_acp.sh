#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --project "${SCRIPT_DIR}/../apps/server" \
  python -m polynoia.installers.deepseek_harness
