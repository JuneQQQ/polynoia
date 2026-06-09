#!/usr/bin/env bash
# Official AgentHub demo initializer.
#
# One command produces a clean, production-like launch-readiness workspace:
#   - wipes and rebuilds the local DB schema
#   - starts backend :7780 and frontend :5173
#   - seeds realistic go-live conversations: release notes, QA workbook,
#     telemetry readiness, status page, @ routing, conflict, diff/history,
#     and recovery cases
#
#   bash scripts/testkit/reset.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRV="$REPO/apps/server"
WEB="$REPO/apps/web"
PY="$SRV/.venv/bin/python"
PNPM="${PNPM:-pnpm}"

stop_launchd_service() {
  local label="$1"
  if command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  fi
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
  echo "  --- /tmp/polynoia_server.log ---" >&2
  tail -80 /tmp/polynoia_server.log >&2 2>/dev/null || true
  echo "  --- /tmp/polynoia_web.log ---" >&2
  tail -80 /tmp/polynoia_web.log >&2 2>/dev/null || true
  return 1
}

start_dev_services() {
  if command -v launchctl >/dev/null 2>&1 && [ "$(uname -s)" = "Darwin" ]; then
    local uid
    local pnpm_bin
    uid="$(id -u)"
    pnpm_bin="$(command -v "$PNPM")"
    mkdir -p /tmp/polynoia-launch
    cat > /tmp/polynoia-launch/server.plist <<PLIST
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
  <key>StandardOutPath</key><string>/tmp/polynoia_server.log</string>
  <key>StandardErrorPath</key><string>/tmp/polynoia_server.log</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
PLIST
    cat > /tmp/polynoia-launch/web.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.polynoia.web</string>
  <key>WorkingDirectory</key><string>$WEB</string>
  <key>ProgramArguments</key><array>
    <string>$pnpm_bin</string>
    <string>dev</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>5173</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH</string>
  </dict>
  <key>StandardOutPath</key><string>/tmp/polynoia_web.log</string>
  <key>StandardErrorPath</key><string>/tmp/polynoia_web.log</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
PLIST
    launchctl bootstrap "gui/$uid" /tmp/polynoia-launch/server.plist
    launchctl bootstrap "gui/$uid" /tmp/polynoia-launch/web.plist
    launchctl kickstart -k "gui/$uid/local.polynoia.server" >/dev/null 2>&1 || true
    launchctl kickstart -k "gui/$uid/local.polynoia.web" >/dev/null 2>&1 || true
  else
    nohup "$PY" -m uvicorn polynoia.main:app --app-dir "$SRV" --host 0.0.0.0 --port 7780 \
      > /tmp/polynoia_server.log 2>&1 &
    nohup "$PNPM" --dir "$WEB" dev --host 0.0.0.0 --port 5173 \
      > /tmp/polynoia_web.log 2>&1 &
  fi
}

echo "→ Stop existing dev services"
stop_launchd_service local.polynoia.server
stop_launchd_service local.polynoia.web
stop_port 7780
stop_port 5173
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
wait_http frontend http://127.0.0.1:5173

echo "→ Seed realistic launch-readiness cases"
"$PY" - <<'PYSEED'
import json
import urllib.error
import urllib.request

API = "http://localhost:7780"
OPUS = "claude-opus-4-7"
SONNET = "claude-sonnet-4-6"
CODEX_MODEL = "gpt-5.5"
OPENCODE_MODEL = "opencode-go/glm5.1"


def req(path, body, method="POST"):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(
        API + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(r) as resp:
        return json.load(resp)


def get(path):
    with urllib.request.urlopen(API + path) as resp:
        return json.load(resp)


def post_task(conv_id, task):
    return req(
        "/api/messages",
        {
            "conv_id": conv_id,
            "sender_id": "you",
            "payload": {"kind": "text", "body": [{"t": "p", "c": task}]},
        },
    )


TEAM = [
    {
        "adapter_id": "claudeCode",
        "name": "阿核",
        "model": OPUS,
        "tagline": "项目协调 · 拆解+验收",
        "tool_role": "orchestrator",
    },
    {
        "adapter_id": "claudeCode",
        "name": "文澜",
        "model": SONNET,
        "tagline": "文档/报告/纪要",
        "tool_role": "writer",
    },
    {
        "adapter_id": "codex",
        "name": "制图",
        "model": CODEX_MODEL,
        "tagline": "网页/视觉/交互",
        "tool_role": "designer",
    },
    {
        "adapter_id": "opencoder",
        "name": "数擎",
        "model": OPENCODE_MODEL,
        "tagline": "数据/脚本/Excel/分析",
        "tool_role": "generalist",
    },
]


CASES = [
    (
        "launch_page",
        "AgentHub 上线 · 发布页",
        "solo:designer",
        "为 AgentHub 的上线准备做一个真实可用的 launch.html 单页发布说明页。要求单文件、自包含、移动端优先。"
        "内容包括:产品定位、上线范围、核心亮点(多 Agent 群聊、产物预览、Diff/回退、部署链接)、目标用户、"
        "3 步快速开始、上线风险提示、反馈入口占位。视觉要像正式上线页,不要像 demo。最后 present 这个 .html。",
    ),
    (
        "release_notes",
        "AgentHub 上线 · Release Notes",
        "solo:writer",
        "整理一份正式上线用的 release-notes.md 并 present。结构:版本摘要、面向用户的新能力、已修复问题、"
        "已知限制、升级/回滚说明、客服/反馈口径。内容基于以下事实:本版本支持 Web/桌面/移动轻量查看;"
        "支持 Claude Code、Codex、OpenCode 适配器;群聊由阿核协调分工;子 Agent 可产出代码 diff、文件、"
        "网页预览、Office 文档;已补齐归档列表刷新、移动端输入框与键盘间距、重连状态栏避让系统状态栏、"
        "工具错误块刷新保留、present 展示部署链接。",
    ),
    (
        "qa_workbook",
        "AgentHub 上线 · QA 检查表",
        "solo:generalist",
        "用 openpyxl 生成一个真正的 launch-qa-checklist.xlsx 并 present。工作簿至少 3 张表:"
        "1) SmokeCases:模块/用例/步骤/预期/负责人/状态/阻塞原因;2) RiskRegister:风险/概率/影响/缓解方案/"
        "owner/上线前是否必须关闭;3) Metrics:上线观测指标/埋点名/阈值/告警渠道。用 Excel 公式统计通过率、"
        "阻塞数、Must Fix 数;加条件格式或醒目标记。内容要像真实上线检查表。",
    ),
    (
        "status_page",
        "AgentHub 上线 · 状态页组件",
        "solo:designer",
        "实现一个单文件 status.html,用于上线当天投屏查看 AgentHub 服务状态。页面展示:前端、后端 API、"
        "WebSocket、Agent 适配器、文件预览、部署链接 6 个模块的状态卡;包含模拟的最近 8 条事件时间线、"
        "刷新按钮、轻量筛选(全部/异常/已恢复)。不依赖外网,移动端和桌面端都要好看。最后 present 这个 .html。",
    ),
    (
        "telemetry_report",
        "AgentHub 上线 · 埋点验收报告",
        "solo:generalist",
        "自造一份约 200 行的 AgentHub 上线前埋点事件 CSV(event_name/user_role/platform/success/latency_ms/"
        "timestamp),用 pandas 做验收分析:关键漏斗(创建项目→新建对话→发送消息→工具调用→present)、"
        "平台成功率、P95 延迟、失败 Top 原因。用 matplotlib 画 2 张图并写 telemetry-readiness.md 报告,"
        "报告内嵌图片引用。最后 present telemetry-readiness.md。",
    ),
    (
        "go_live_pack",
        "AgentHub 上线 · Go-live 协作包",
        "group",
        "群里多位负责人并行准备 AgentHub 上线 Go-live 协作包。请拆成互不依赖的章节交给成员:"
        "发布范围与非目标、上线检查清单、灰度与回滚方案、客服/公告口径、上线当天值班与监控。"
        "每章落到 sections/ 下独立文件,最后由阿核合并成 go-live.md 并 present。注意:共享文件 go-live.md "
        "的骨架/目录只由一个人建,其他人填各自章节,避免合并冲突。",
    ),
    (
        "mention_seq",
        "@路由 · 上线依赖接力",
        "group",
        "这是 @ 路由回归测试,用真实上线准备场景。@文澜 @制图 协作,但必须严格顺序执行:"
        "第一阶段只让文澜创建 sections/release-copy.md,内容是一段 80 字以内的上线公告文案;"
        "等第一阶段完成并合并到 main 后,第二阶段再让制图读取该文件,并创建 sections/release-banner.html,"
        "把这段文案做成一个上线横幅组件。不要 present,最后由阿核说明两个文件是否都在 main。"
        "如果你要派活,必须分阶段 dispatch;不要让两人并发。",
    ),
    (
        "mention_single",
        "@路由 · 单点名直达+验收",
        "group",
        "这是单 @ 路由回归测试。请 @制图 直接做一个极小的上线倒计时组件:sections/launch-countdown.html,"
        "页面只需要显示“AgentHub launch readiness”和一个开始检查按钮。预期:制图先直达执行并 clean merge 到 main,"
        "随后阿核收到轻量验收回合。完成后 present 这个 HTML。",
    ),
    (
        "mention_unknown",
        "@路由 · 未知成员忽略",
        "group",
        "这是未知 @ 边界测试。请 @不存在的成员 @文澜 起草 sections/unknown-mention-policy.md,"
        "内容说明上线群聊中只有真实群成员会被解析,未知 @ 不应触发任何 agent。最终只需要一个 Markdown 文件,"
        "不要 present。",
    ),
    (
        "conflict_same_file",
        "合并冲突 · 上线口径冲突",
        "group",
        "这是冲突处理回归测试。请故意让文澜和制图并行修改同一个文件 sections/launch-owner.md 的同一行:"
        "文澜写“launch_owner = 文档负责人”,制图写“launch_owner = 前端负责人”。不要提前规避冲突;"
        "让系统暴露真实冲突,然后由阿核按冲突卡流程选择一个最终版本并说明处理结果。",
    ),
    (
        "single_main_sync",
        "单聊合并 · 上线文件同步",
        "solo:designer",
        "这是单聊 main 同步回归测试。创建 sections/single-agent-launch-sync.md,内容三行:"
        "title: single agent launch sync / agent: 制图 / status: ready。完成后 report,不要 present。",
    ),
    (
        "diff_history",
        "Diff/历史 · 上线清单连续修改",
        "solo:designer",
        "这是 diff 与提交历史回归测试。先创建 launch-history-smoke.md,写 3 行初始上线检查项;"
        "然后在同一轮里修改它两次:第一次追加 rollback checklist,第二次把标题改成 Launch History Smoke Final。"
        "目标是产生可检查的 diff/历史记录;完成后 report,不要 present。",
    ),
    (
        "readonly_recovery",
        "工具错误 · 上线缺失文件恢复",
        "solo:generalist",
        "这是错误恢复边界测试。先尝试读取 missing/launch-runbook.md,确认失败后不要停;"
        "随后创建 sections/launch-recovery.md,内容写明 recovered after missing launch runbook。完成后 report。",
    ),
]


def main():
    for adapter_id in ("claudeCode", "codex", "opencoder"):
        req(f"/api/agents/{adapter_id}/enable", {})

    existing_agents = {a["name"]: a for a in get("/api/agents")}
    ids = {}
    for contact in TEAM:
        existing = existing_agents.get(contact["name"])
        ids[contact["name"]] = existing["id"] if existing else req("/api/contacts", contact)["contact"]["id"]

    by_role = {
        "orchestrator": ids["阿核"],
        "writer": ids["文澜"],
        "designer": ids["制图"],
        "generalist": ids["数擎"],
    }
    all_members = list(ids.values())
    existing_workspaces = {w["name"]: w for w in get("/api/workspaces")}
    existing_convs = {(c.get("workspace_id"), c["title"]): c for c in get("/api/conversations")}
    manifest = []

    for key, title, who, task in CASES:
        ws_name = f"上线准备 · {title}"
        ws_row = existing_workspaces.get(ws_name)
        if ws_row:
            ws_id = ws_row["id"]
        else:
            ws_id = req(
                "/api/workspaces",
                {
                    "name": ws_name,
                    "desc": f"AgentHub 上线准备真实用例:{title}",
                    "members": all_members,
                    "color": "#D97757",
                },
            )["workspace"]["id"]
            existing_workspaces[ws_name] = {"id": ws_id, "name": ws_name}

        conv_title = f"上线准备 · {title}"
        existing_conv = existing_convs.get((ws_id, conv_title))
        if who == "group":
            orch = by_role["orchestrator"]
            conv = existing_conv or req(
                "/api/conversations",
                {
                    "workspace_id": ws_id,
                    "title": conv_title,
                    "members": ["you"] + all_members,
                    "group": True,
                    "direct": False,
                    "member_roles": {
                        orch: "拆解 + 验收 + 合并 present",
                        by_role["writer"]: "上线文档/公告/客服口径",
                        by_role["designer"]: "上线页面/状态组件/视觉检查",
                        by_role["generalist"]: "QA 表格/埋点/数据分析",
                    },
                    "orchestrator_member_id": orch,
                },
            )
            members = ["you"] + all_members
        else:
            role = who.split(":", 1)[1]
            agent = by_role[role]
            conv = existing_conv or req(
                "/api/conversations",
                {
                    "workspace_id": ws_id,
                    "title": conv_title,
                    "members": ["you", agent],
                    "group": True,
                    "direct": False,
                    "member_roles": {agent: "独立完成本上线准备任务"},
                },
            )
            members = ["you", agent]

        if not existing_conv:
            post_task(conv["id"], task)
        manifest.append({"key": key, "conv_id": conv["id"], "title": title, "members": members})

    print("  cases(" + str(len(manifest)) + "): " + ", ".join(item["key"] for item in manifest))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print("HTTPError", e.code, e.read().decode()[:400])
        raise
PYSEED

CONVS="$(sqlite3 ~/.polynoia/polynoia.db 'SELECT count(*) FROM conversations')"
AGENTS="$(sqlite3 ~/.polynoia/polynoia.db "SELECT name || ':' || json_extract(setup, '$.adapter_id') || '/' || json_extract(setup, '$.model') FROM agents WHERE custom = 1 ORDER BY name")"
echo "✓ Ready — $CONVS production-like launch-readiness conversations"
echo "$AGENTS"
echo "  Frontend: http://127.0.0.1:5173"
