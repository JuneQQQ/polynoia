# 夜间自主开发进展 — 2026-05-30(凌晨)

> 用户授权:重排 TODO + 直接深入开发,把项目做完整、前后端闭环、补全界面、
> 让 agent 协作更闭环/健壮/丰富。本文 = 早上 review 的索引。**全程 pytest 119
> 绿 / tsc 干净 / 新增代码 lint 干净 / 服务保持可用。** 未 commit(留作你 review diff)。

## 起点:两份并行评估(workflow 产出)
- 全项目完整性扫描:32 个 gap(17 个断回路)+ RuFlo 深研:8 项协作升级。
- 评估剔除了被高估的"伪 gap"(会话删除/CodeTab 写回/建项目/merge-mode/群 orchestrator 校验已存在)。

## 已完成(本夜)

### 0. 反"卡顿"(用户最初痛点)
- **interleaved thinking**:Claude adapter 开 `thinking=enabled, budget 8000` → orchestrator/coder
  在工具调用之间也流式思考,填补静默空档(`adapters/claude_code.py`)。
- **工具开始即出卡**:`content_block_start(tool_use)` 立即发 running 卡 → 大 `dispatch`
  生成参数那 20-30s 不再死寂,最终块原地更新同一张卡(`adapters/claude_code.py` + 测试)。
- **思考 ticker**:`ReasoningPart` 流式时只显示底部最新几行 + 顶部渐隐(往下滑),完成显示「思考 N 秒」。

### 1. Batch A — 导航解锁 + 消灭所有 UI stub(directive #1)
- **Phase 0**:Inbox / 广场(CreateHub)/ 归档 三个**已建但不可达**的视图接上入口
  (Sidebar Footer 导航条 + 折叠 rail 图标),修了无 onClick 的死 Settings 键(`Sidebar.tsx`、`App.tsx`)。
- **Phase 4**:`自定义 Agent` 的 **NotYetModal stub** → 复用真实的 `NewContactModal`(走 POST /api/contacts)。
- **Phase 2**:加 **PATCH /api/conversations/{id}/members**(repo.set_members + 校验 orchestrator 不可移除 + system 事件 + 广播);
  `MembersListView` 的 **alert stub** → 真实 add(picker)+ remove(每行 ×),App 监听事件实时同步成员到 ChatPane。
- **Phase 3**:CreateHub 联系人行加 **编辑/删除**(custom only,接已存在的 deleteContact + NewContactModal edit 模式);
  会话 ⋯ 菜单加 **归档/取消归档**(接已存在的 archiveConv,ArchiveView 已能列归档)。

### 2. Batch B — ask-form 闭环(directive #1)
- ask-form 块**持久化**成消息(刷新不丢);新 **GET /ask-forms** 返回"未答"的(末条 user 消息之后);
  `AskFormsPanel` useEffect 水合(`routes.py`、`api.ts`、`AskFormsPanel.tsx`)。

### 3. Batch C — RuFlo 协作加固(directive #2)→ 见 **ADR-015**
- 新 **`recall`** 工具(执行中读 blackboard)+ **`report`** 工具(闭环交付自评 verdict → conv_memory → summary 读回)。
- 新 **`critic`** 只读审查角色;worker spawn 加 report 硬性收尾 nudge;summary 加验收/升级 nudge(不盲信、失败必点名)。
- 修 **静默卡死**:adapter-unavailable 早退现在也标 burst lane failed(否则 burst 永不收尾)。
- 测试:`tests/storage`(set_members ×3)、`tests/adapters`(thinking/tool-card/incremental-fold)、`tests/mcp`(22)全绿。

### 早期(本会话稍早)已落地
- 执行与连接解耦 + Agent 级终止(8 个并发缺陷修复 + 对抗复审 0 新 bug + e2e PASS)。
- 思考块捕获三 adapter + reasoning 三层传输 + 折叠 UI + 状态阶段回显(正在思考/执行/回复)。
- `python3 scripts/seed_demo.py` 现在是一键硬重置(自举进 server uv 环境);reset_db.py 成薄 alias。

### 4. Batch D — 附件上传闭环(directive #1)✅
- 新 **POST /api/upload**(裸字节,免 multipart 依赖)+ **GET /api/files/{id}/raw**;Composer 改成
  **先上传拿 URL** 再附,消息 payload 存短 URL(不再 inline base64)→ 刷新可重渲;上传失败 alert 不静默。
  **live round-trip 已验证**(POST→url→GET 取回字节)。`routes.py`、`api.ts`、`Composer.tsx`、`ChatPane.tsx`。
- **Web 预览 Phase 6 无需做**:mock URL 在 `orchestrator/runtime.py`,而 `OrchestratorRuntime` 是**死代码**
  (只在注释被提及,从不 import);真实预览早经 `/api/workspaces/{id}/preview` 工作。

## 仍待办(优先级排序,留给后续)
1. **附件 → agent 可见**(中):上传闭环已通,但 dispatch 还没把附件 ref 注入 agent prompt
   (需改 prompt 组装)。当前 agent 看不到用户贴的图——要么注入,要么 UI 明确标注。
2. **删死代码**:`orchestrator/runtime.py`(确认无 import,可删)。
2. **RuFlo 余项**(中/需复审):per-task 自动重试(retry_count 已有字段待接,transient/permanent 分类);
   burst watchdog(给死掉的 lane 兜底,需谨慎接入已验证的 burst 生命周期);依赖 DAG(`after` on dispatch,需新 ADR);
   contract 原子写穿(修刷新丢契约的窄窗口)。
3. **report 上 lane chip**(把 verdict 显示在 burst 泳道上,需给 TaskItem 加 verdict 字段 + 前端渲染)。
4. 清理:未用的 TypingPayload、`ws.queryAgentStatus`。

## 验证基线
- `cd apps/server && uv run pytest -q` → **119 passed, 5 skipped**。
- `cd apps/web && ./node_modules/.bin/tsc -b` → **clean**。
- 新增代码 biome/ruff 无新增告警(既有告警是历史遗留:import 排序 / B904 / 工作区 helper 的字符串注解)。
- 服务在所有热重载后保持 UP。重置:`python3 scripts/seed_demo.py`。
