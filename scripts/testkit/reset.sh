#!/usr/bin/env bash
# 一键重置本地 Polynoia 库:整库清空 + 重建 schema + 重种 testkit 测试用例。
# 幂等(每次跑完都是干净的 testkit 用例集)。会清掉所有现有会话(含任何旧 run)。
# macOS 上 direct-creds 自动开启,重启后 claude/agent 仍能登录运行。
#
#   bash scripts/testkit/reset.sh
set -euo pipefail

REPO="/Users/june/polynoia-test/repo"
SRV="$REPO/apps/server"
PY="$SRV/.venv/bin/python"

echo "→ 停掉 :7780 上的服务器"
PID="$(lsof -nP -iTCP:7780 -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
if [ -n "${PID:-}" ]; then kill "$PID"; sleep 1; echo "  killed $PID"; else echo "  (没在跑)"; fi

echo "→ 清库 + 重建 schema"
cd "$SRV"
PYTHONPATH="$SRV" "$PY" - <<PYEOF
import asyncio, sys
sys.path.insert(0, "$REPO/scripts")
import seed_demo
asyncio.run(seed_demo._wipe_and_bootstrap())
print("  wiped + bootstrapped")
PYEOF

echo "→ 启动服务器(给 claude/codex/opencode agent 走 7897 代理出墙;localhost 直连)"
# Spawned agents (Claude Code / Codex / OpenCode) inherit these via Sandbox.env_for_agent.
# NO_PROXY MUST include localhost — the MCP subprocess + dispatch/report tools
# call back to 127.0.0.1:7780; routing those through the proxy would break them.
PROXY="${POLYNOIA_AGENT_PROXY:-http://127.0.0.1:7897}"
export HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" ALL_PROXY="$PROXY"
export http_proxy="$PROXY" https_proxy="$PROXY" all_proxy="$PROXY"
export NO_PROXY="localhost,127.0.0.1,0.0.0.0,::1" no_proxy="localhost,127.0.0.1,0.0.0.0,::1"
nohup "$SRV/.venv/bin/uvicorn" polynoia.main:app --host 0.0.0.0 --port 7780 \
  > /tmp/polynoia_server.log 2>&1 &
for i in $(seq 1 20); do
  code="$(curl -s -m 2 http://127.0.0.1:7780/api/agents -o /dev/null -w '%{http_code}' 2>/dev/null || true)"
  [ "$code" = "200" ] && { echo "  up after ${i}s"; break; }
  sleep 1
done

echo "→ 重种 testkit 测试用例"
MANIFEST_OUT="/tmp/polynoia_testkit_manifest.jsonl"
"$PY" "$REPO/scripts/testkit/_more_seed.py" > "$MANIFEST_OUT"

CONVS="$(sqlite3 ~/.polynoia/polynoia.db 'SELECT count(*) FROM conversations')"
AGENTS="$(sqlite3 ~/.polynoia/polynoia.db "SELECT name || ':' || json_extract(setup, '$.adapter_id') || '/' || json_extract(setup, '$.model') FROM agents WHERE custom = 1 ORDER BY name")"
"$PY" - <<PYEOF
import json
from pathlib import Path
path = Path("$MANIFEST_OUT")
line = next((ln for ln in path.read_text().splitlines() if ln.startswith("MANIFEST=")), "")
items = json.loads(line.split("=", 1)[1]) if line else []
print("  用例(" + str(len(items)) + "): " + ", ".join(item["key"] for item in items))
PYEOF
echo "✓ 重置完成 — $CONVS 个会话(干净的 testkit 用例)"
echo "$AGENTS"
