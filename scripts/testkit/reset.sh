#!/usr/bin/env bash
# Official AgentHub demo initializer.
#
# One command produces clean, production-like real-project workspaces:
#   - wipes and rebuilds the local DB schema
#   - starts backend :7780 and frontend :7788
#   - seeds realistic deliverable conversations: web games, PPT, DOCX, Excel,
#     React/Vue + backend API apps, data reports, office deliverables,
#     collaboration, conflict, diff/history, and recovery cases
#
#   bash scripts/testkit/reset.sh
set -euo pipefail

REPO="${POLYNOIA_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SRV="$REPO/apps/server"
WEB="$REPO/apps/web"
PY="$SRV/.venv/bin/python"
PNPM="${PNPM:-pnpm}"
RUN_DIR="$REPO/.tmp/testkit"
LAUNCH_DIR="$RUN_DIR/launchd"
SERVER_LOG="$RUN_DIR/polynoia_server.log"
WEB_LOG="$RUN_DIR/polynoia_web.log"
SERVER_PLIST="$LAUNCH_DIR/server.plist"
WEB_PLIST="$LAUNCH_DIR/web.plist"

mkdir -p "$RUN_DIR" "$LAUNCH_DIR"

stop_launchd_service() {
  local label="$1"
  command -v launchctl >/dev/null 2>&1 || return 0
  local target="gui/$(id -u)/$label"
  # `bootout` can race KeepAlive respawn and silently leave the label loaded —
  # the next `bootstrap` then dies with "Bootstrap failed: 5: Input/output error".
  # So bootout + VERIFY the label is actually gone (poll). Do NOT use
  # `launchctl disable` here: it persists a disabled flag that makes the later
  # `bootstrap` fail (also "5: Input/output error"). Once booted out the service
  # is unloaded and KeepAlive no longer respawns it.
  for _ in 1 2 3 4 5; do
    launchctl print "$target" >/dev/null 2>&1 || return 0  # not loaded → done
    launchctl bootout "$target" >/dev/null 2>&1 || true
    sleep 0.5
  done
}

stop_port() {
  local port="$1"
  local pids
  pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
    echo "  stopped :$port ($pids)"
  else
    echo "  :$port was not running"
  fi
}

wait_http() {
  local name="$1"
  local url="$2"
  local expected="${3:-200}"
  for i in $(seq 1 40); do
    local code
    code="$(curl -s --noproxy '*' -m 2 "$url" -o /dev/null -w '%{http_code}' 2>/dev/null || true)"
    if [ "$code" = "$expected" ]; then
      echo "  $name up after ${i}s"
      return 0
    fi
    sleep 1
  done
  echo "  $name failed to start: $url" >&2
  echo "  --- $SERVER_LOG ---" >&2
  tail -80 "$SERVER_LOG" >&2 2>/dev/null || true
  echo "  --- $WEB_LOG ---" >&2
  tail -80 "$WEB_LOG" >&2 2>/dev/null || true
  return 1
}

start_dev_services() {
  if command -v launchctl >/dev/null 2>&1 && [ "$(uname -s)" = "Darwin" ]; then
    local uid
    local pnpm_bin
    uid="$(id -u)"
    pnpm_bin="$(command -v "$PNPM")"
    mkdir -p "$LAUNCH_DIR"
    cat > "$SERVER_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.polynoia.server</string>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string>
    <string>-m</string><string>uvicorn</string>
    <string>polynoia.main:app</string>
    <string>--app-dir</string><string>$SRV</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>7780</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH</string>
    <key>HTTP_PROXY</key><string>$HTTP_PROXY</string>
    <key>HTTPS_PROXY</key><string>$HTTPS_PROXY</string>
    <key>ALL_PROXY</key><string>$ALL_PROXY</string>
    <key>NO_PROXY</key><string>$NO_PROXY</string>
  </dict>
  <key>StandardOutPath</key><string>$SERVER_LOG</string>
  <key>StandardErrorPath</key><string>$SERVER_LOG</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
PLIST
    cat > "$WEB_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.polynoia.web</string>
  <key>WorkingDirectory</key><string>$WEB</string>
  <key>ProgramArguments</key><array>
    <string>$pnpm_bin</string>
    <string>dev</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>7788</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH</string>
  </dict>
  <key>StandardOutPath</key><string>$WEB_LOG</string>
  <key>StandardErrorPath</key><string>$WEB_LOG</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
PLIST
    launchctl bootstrap "gui/$uid" "$SERVER_PLIST"
    launchctl bootstrap "gui/$uid" "$WEB_PLIST"
    launchctl kickstart -k "gui/$uid/local.polynoia.server" >/dev/null 2>&1 || true
    launchctl kickstart -k "gui/$uid/local.polynoia.web" >/dev/null 2>&1 || true
  else
    nohup "$PY" -m uvicorn polynoia.main:app --app-dir "$SRV" --host 0.0.0.0 --port 7780 \
      > "$SERVER_LOG" 2>&1 &
    nohup "$PNPM" --dir "$WEB" dev --host 0.0.0.0 --port 7788 \
      > "$WEB_LOG" 2>&1 &
  fi
}

echo "→ Stop existing dev services"
stop_launchd_service local.polynoia.server
stop_launchd_service local.polynoia.web
# Legacy labels from older revisions of this script — clear them too so a
# stale registration can't block bootstrap.
stop_launchd_service local.polynoia.dev-server
stop_launchd_service local.polynoia.dev-web
stop_port 7780
stop_port 7788

# Reap leftover DELIVERABLE services started by agents inside sandbox worktrees
# (vite / uvicorn / node dev servers under ~/sandbox/polynoia/workspaces/...).
# Without this they survive a reset, keep holding contract ports (5173/8000…),
# and the NEXT run's agents waste turns fighting "address already in use" or —
# worse — kill each other's processes. Match strictly on the sandbox path so
# nothing outside agent worktrees can be hit.
echo "→ Reap leftover sandbox deliverable services"
SANDBOX_ROOT="${POLYNOIA_SANDBOX_ROOT:-$HOME/sandbox/polynoia}"
reaped=0
while IFS= read -r pid; do
  [ -n "$pid" ] || continue
  kill "$pid" 2>/dev/null && reaped=$((reaped+1)) || true
done < <(ps -axo pid=,command= | grep -F "$SANDBOX_ROOT/workspaces/" | grep -vE "grep|$0" | awk '{print $1}')
if [ "$reaped" -gt 0 ]; then
  sleep 1
  # Escalate to SIGKILL for any survivor still under the sandbox root.
  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    kill -9 "$pid" 2>/dev/null || true
  done < <(ps -axo pid=,command= | grep -F "$SANDBOX_ROOT/workspaces/" | grep -vE "grep|$0" | awk '{print $1}')
  echo "  reaped $reaped sandbox service(s)"
else
  echo "  no sandbox services running"
fi
sleep 1

echo "→ Wipe DB and rebuild schema"
cd "$SRV"
PYTHONPATH="$SRV" "$PY" - <<PYEOF
import asyncio, sys
sys.path.insert(0, "$REPO/scripts")
import seed_demo
asyncio.run(seed_demo._wipe_and_bootstrap())
print("  schema ready")
PYEOF

echo "→ Start backend and frontend"
PROXY="${POLYNOIA_AGENT_PROXY:-http://127.0.0.1:7897}"
export HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" ALL_PROXY="$PROXY"
export http_proxy="$PROXY" https_proxy="$PROXY" all_proxy="$PROXY"
export NO_PROXY="localhost,127.0.0.1,0.0.0.0,::1" no_proxy="localhost,127.0.0.1,0.0.0.0,::1"
start_dev_services
wait_http backend http://127.0.0.1:7780/api/agents
wait_http frontend http://127.0.0.1:7788

echo "→ Seed realistic, effect-driven user cases"
"$PY" "$REPO/scripts/testkit/seed_cases.py"
echo "  Frontend: http://127.0.0.1:7788"
