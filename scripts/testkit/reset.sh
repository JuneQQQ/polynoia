#!/usr/bin/env bash
# Official AgentHub demo initializer.
#
# One command produces clean, production-like real-project workspaces:
#   - wipes and rebuilds the local DB schema
#   - starts backend :7780 and frontend :5173
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
    <string>--port</string><string>5173</string>
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
    nohup "$PNPM" --dir "$WEB" dev --host 0.0.0.0 --port 5173 \
      > "$WEB_LOG" 2>&1 &
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

echo "→ Seed realistic real-project cases"
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
        "react_plane_war",
        "React 飞机大战小游戏",
        "solo:designer",
        "用 React + Vite 开发一个飞机大战小游戏,不要只写单个 HTML。产物放到 app/ 目录:"
        "package.json、src/App.jsx、src/main.jsx、src/styles.css。要求 Canvas 或 DOM 动画均可,"
        "支持键盘和移动端触摸拖拽,包含玩家飞机、敌机、子弹、碰撞、分数、生命值、暂停/重新开始。"
        "请补一个 README.md 写明 npm install / npm run dev,并 present 可预览入口。",
    ),
    (
        "fullstack_issue_tracker",
        "React + FastAPI 缺陷追踪系统",
        "group",
        "协作开发一个小型缺陷追踪系统,最终能本地启动前后端并 present。技术要求:"
        "frontend/ 使用 React + Vite,backend/ 使用 FastAPI。后端提供 Issue 的列表/创建/状态流转 API,"
        "用 SQLite 或 JSON 文件持久化;前端有列表、筛选、新建弹窗、详情抽屉和状态切换。"
        "文澜写产品规格和接口契约;数擎实现后端与测试数据;制图实现前端;阿核验收 API 联调、main 同步和 README 启动说明。",
    ),
    (
        "family_budget_xlsx",
        "三口之家月度预算 Excel",
        "solo:generalist",
        "用 openpyxl 生成一个真正的 budget.xlsx 并 present。工作簿包含:收支明细、分类汇总、月度结余、"
        "支出占比饼图。数字要像真实三口之家月度账单,分类清晰,汇总表使用 Excel SUMIF/SUM 公式,加基本格式和条件标记。",
    ),
    (
        "vue_inventory_admin",
        "Vue + Express 库存管理后台",
        "group",
        "开发一个库存管理后台,技术栈使用 Vue 3 + Vite 前端和 Express 后端。最终目录包含 frontend/、backend/、README.md。"
        "后端提供商品 CRUD、库存入库/出库、低库存预警接口,用 JSON 文件模拟数据库;前端包含商品表格、搜索筛选、"
        "新增/编辑表单、库存流水和低库存高亮。文澜先写接口与验收标准;数擎实现后端;制图实现 Vue 前端;阿核做联调验收并 present。",
    ),
    (
        "rental_contract_docx",
        "房屋租赁合同 DOCX",
        "solo:writer",
        "生成一份正式的房屋租赁合同 docx 并 present。包含出租方/承租方信息、房屋信息、租期、租金押金、"
        "维修责任、违约责任、退租交接、附件清单和签字页。要求版式像正式合同,有标题层级、表格和签署栏。",
    ),
    (
        "startup_pitch_ppt",
        "AI 教育创业路演 PPT",
        "solo:writer",
        "制作一份 10 页左右的 AI 教育创业项目路演 PPTX 并 present。内容包括:封面、痛点、解决方案、"
        "产品截图占位、市场规模、商业模式、竞品对比、增长策略、财务预测、团队与融资计划。要求视觉统一、标题短、每页信息密度合理。",
    ),
    (
        "ops_status_dashboard",
        "React + 后端状态页",
        "group",
        "做一个上线状态页项目,要求包含 backend/ 和 frontend/。backend/ 提供服务状态、最近事故、订阅邮箱模拟接口;"
        "frontend/ 用 React + Vite 展示总体状态、组件状态、事故时间线、订阅表单和移动端布局。"
        "数擎负责 API 和 mock 数据;制图负责前端;文澜负责事故文案和 README;阿核验收前后端联调、错误态和 present。",
    ),
    (
        "upload_doc_review",
        "合同文档上传审阅系统",
        "group",
        "开发一个合同文档上传审阅系统,覆盖真实文件上传链路。frontend/ 用 React + Vite,backend/ 用 FastAPI。"
        "前端必须支持拖拽上传 DOCX/PDF、上传进度、最大 20MB 限制、上传中刷新/关闭页面警告、失败重试和已上传文件列表。"
        "后端提供 /upload、/files、/files/{id}/summary 接口,用本地 uploads/ 保存文件并返回模拟摘要/风险点。"
        "文澜写产品规则和风险点模板;数擎实现上传 API 和文件元数据;制图实现拖拽上传 UI;阿核验收多文件、超限、刷新保护和 present。",
    ),
    (
        "multimodal_receipt_expense",
        "票据图片识别报销流",
        "group",
        "做一个多模态票据报销原型。要求支持上传或拖拽多张发票/小票图片,前端展示缩略图、识别状态、"
        "可编辑字段(金额/商户/日期/分类)和汇总表;后端提供图片上传、OCR 模拟结果、提交报销单接口。"
        "可以在 sample-assets/ 生成几张 SVG/PNG 小票样例用于测试,不要依赖外网。数擎做后端和样例数据,制图做前端,文澜写验收说明,阿核验收。",
    ),
    (
        "marketing_page_sequence",
        "英语夏令营落地页",
        "group",
        "协作完成一个英语夏令营招生落地页 summer-camp.html 并 present。必须顺序接力:"
        "文澜先写招生卖点、课程安排、家长 FAQ 到 sections/summer-copy.md;"
        "制图随后读取文案并实现单文件落地页。阿核负责控制顺序和验收。",
    ),
    (
        "single_agent_portfolio",
        "摄影师作品集网站",
        "group",
        "请 @制图 直接做一个摄影师作品集单页 photographer.html。要求包含全屏作品首屏、作品网格、"
        "个人简介、联系入口和移动端适配。制图完成后阿核做轻量验收并 present。",
    ),
    (
        "travel_plan_doc",
        "关西亲子旅行计划",
        "solo:writer",
        "写一份关西 6 天 5 晚亲子旅行计划 travel-plan.md 并 present。包含每日行程、交通、餐厅、预算、"
        "雨天备选、儿童友好提醒和打包清单。内容要具体到景点和时间段。",
    ),
    (
        "sales_analysis_report",
        "电商销售分析报告",
        "group",
        "做一份电商销售分析报告。请 @不存在的成员 @数擎 生成 300 行订单样例 CSV,分析 GMV、客单价、"
        "品类贡献、复购率和退款率,输出 sales-analysis.md 和两张图。未知成员不应被调度;最终由阿核验收。",
    ),
    (
        "csv_upload_dashboard",
        "CSV 上传数据看板",
        "solo:generalist",
        "开发一个 CSV 上传数据看板原型,包含 backend/ 和 frontend/。用户上传 sales.csv 后,系统解析字段,"
        "展示上传进度、字段映射、异常行提示、基础统计和图表。后端限制最大 10MB,返回解析错误行号;"
        "前端需要支持重新上传、取消上传和刷新保护。最后 present 可运行入口或 README。",
    ),
    (
        "image_to_landing_page",
        "参考图生成落地页",
        "solo:designer",
        "做一个多模态设计任务:在 sample-assets/reference-layout.png 里先生成一张简洁参考图,"
        "再根据这张参考图实现 landing-from-image.html。页面需说明它从参考图提取了哪些布局元素,"
        "并实现对应的首屏、卡片区和移动端布局。最后 present HTML。",
    ),
    (
        "restaurant_menu_collab",
        "餐厅点餐系统多人改版",
        "group",
        "协作改版一个小型餐厅点餐系统。要求 frontend/ React 菜单页 + cart 状态,backend/ 提供菜单与下单接口。"
        "为了模拟真实多人编辑冲突,文澜和制图都需要调整同一个菜单标题文案:文澜偏品牌叙事,制图偏视觉短标题。"
        "不要提前规避冲突;如果出现冲突,由阿核选择更适合页面的一版并说明原因,最后 present。",
    ),
    (
        "game_2048",
        "2048 网页小游戏",
        "solo:designer",
        "做一个 2048 网页小游戏 2048.html 并 present。这是保留的单文件预览烟测用例。要求键盘/滑动操作、"
        "计分、重新开始、胜利/失败状态、移动端适配。完成后确保文件已经同步到 main 工作区。",
    ),
    (
        "resume_iteration",
        "个人简历网页连续迭代",
        "solo:designer",
        "制作 personal-resume.html。先完成基础版简历网页,再连续迭代两次:第一次加项目经历和技能标签,"
        "第二次优化移动端排版和配色。最终 present,并保留可检查的提交历史。",
    ),
    (
        "django_like_api_spec",
        "权限管理 API 设计与实现",
        "solo:generalist",
        "实现一个轻量权限管理后端原型,放在 backend/。可用 FastAPI 或 Flask,提供用户、角色、权限、登录模拟和鉴权中间件。"
        "要求包含 OpenAPI/接口说明、pytest 或脚本级测试、示例数据和 README 启动方式。最后 present README 或 API 文档。",
    ),
    (
        "meeting_notes_recovery",
        "会议纪要缺失资料恢复",
        "solo:generalist",
        "整理一份项目复盘会议纪要 meeting-notes.md。先尝试读取 missing/interview-notes.md;如果资料不存在,"
        "不要停,请基于合理假设补齐背景、决策、行动项和负责人,最后 present。",
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
    colors = ["#D97757", "#5AA17F", "#5C7EC7", "#B66A4B", "#8A64D8", "#C0914A"]
    role_names = {
        "writer": "文档/内容/办公",
        "designer": "网页/视觉/交互",
        "generalist": "数据/脚本/Excel/分析",
    }

    for idx, (key, title, who, task) in enumerate(CASES):
        conv_title = title
        if who == "group":
            orch = by_role["orchestrator"]
            members = ["you"] + all_members
            member_roles = {
                orch: "拆解 + 验收 + 合并 present",
                by_role["writer"]: "文案/文档/DOCX/PPT 叙事",
                by_role["designer"]: "网页/游戏/视觉交互实现",
                by_role["generalist"]: "Excel/数据/脚本/分析",
            }
            desc = f"多人协作真实交付项目:{title}"
        else:
            role = who.split(":", 1)[1]
            agent = by_role[role]
            members = ["you", agent]
            member_roles = {agent: "独立完成本真实产物任务"}
            desc = f"{role_names.get(role, '单 Agent')}真实交付项目:{title}"

        ws_row = existing_workspaces.get(title)
        if ws_row:
            workspace_id = ws_row["id"]
        else:
            workspace_id = req(
                "/api/workspaces",
                {
                    "name": title,
                    "desc": desc,
                    "members": members,
                    "color": colors[idx % len(colors)],
                },
            )["workspace"]["id"]
            existing_workspaces[title] = {"id": workspace_id}

        existing_conv = existing_convs.get((workspace_id, conv_title))
        if who == "group":
            conv = existing_conv or req(
                "/api/conversations",
                {
                    "workspace_id": workspace_id,
                    "title": conv_title,
                    "members": members,
                    "group": True,
                    "direct": False,
                    "member_roles": member_roles,
                    "orchestrator_member_id": orch,
                    "draft_text": task,
                },
            )
        else:
            conv = existing_conv or req(
                "/api/conversations",
                {
                    "workspace_id": workspace_id,
                    "title": conv_title,
                    "members": members,
                    "group": False,
                    "direct": True,
                    "member_roles": member_roles,
                    "draft_text": task,
                },
            )

        if existing_conv:
            req(f"/api/conversations/{conv['id']}/draft", {"draft_text": task}, method="PATCH")
        manifest.append(
            {
                "key": key,
                "workspace_id": workspace_id,
                "conv_id": conv["id"],
                "title": title,
                "members": members,
            }
        )

    print("  cases(" + str(len(manifest)) + "): " + ", ".join(item["key"] for item in manifest))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print("HTTPError", e.code, e.read().decode()[:400])
        raise
PYSEED

"$PY" - <<'PYSUMMARY'
import json
import urllib.request

API = "http://localhost:7780"


def get(path):
    with urllib.request.urlopen(API + path) as resp:
        return json.load(resp)


workspaces = get("/api/workspaces")
convs = get("/api/conversations")
agents = [a for a in get("/api/agents") if a.get("custom")]

print(f"✓ Ready — {len(workspaces)} real project workspaces, {len(convs)} conversations")
for agent in sorted(agents, key=lambda x: x.get("name", "")):
    setup = agent.get("setup") or {}
    print(f"{agent.get('name')}:{setup.get('adapter_id')}/{setup.get('model')}")
PYSUMMARY
echo "  Frontend: http://127.0.0.1:5173"
