# 2026-06-12 夜间自主会话 — 信息架构(IA)改造 + 对抗测试驱动修复

> 授权:用户「我要睡觉了,你慢慢干」+「请改造逻辑后,再继续重测,不计成本,允许大力重构」。
> 纪律:仅做有测试背书的安全改动,频繁过门禁(后端 pytest / 前端 tsc+vitest+build),**未经允许不 push github**。
> 全部改动 **UNCOMMITTED**,留待用户醒后视觉验收 UI。

## 一、对抗测试驱动的缺陷修复(全部完成)

两轮 Workflow 对抗测试共暴露 ~10 个真实缺陷,逐一**测试先行**修复(失败的刁钻测试=胜利;绝不削弱测试换绿)。

| # | 缺陷 | 修复 |
|---|------|------|
| BUG#1 | pending-edit 决策非原子(并发双裁决) | `set_pending_edit_status` 改条件 UPDATE(`where status=='pending'`)+ 进程内 per-loop 锁(`_decide_locks[id(loop)]`,解决 pytest per-test-loop 绑定问题) |
| BUG#2 | 重启后 burst tasks 泳道悬挂非终态 | 新增 `reap_orphan_burst_tasks` 启动 reaper,翻非终态 `tasks` 泳道为 `failed` |
| BUG#3 | MCP 工具超时未标 isError(被读成"完成") | `_wrap_result`:`kind=="error" OR timed_out is True` → `CallToolResult(isError=True)` |
| C1 | 沙箱路径逃逸 | `_fs_paths.py`:conv 分支 `resolve()` + `relative_to(sandbox_root)` confinement,逃逸→400 |
| H1 | NUL 字节 / 畸形路径崩溃 | `_resolve_safe_path` try/except ValueError→400 |
| H2 | 分页游标丢行(同毫秒>limit 行时无法翻到会话起点) | 复合游标 `(created_at, id)`:`list_messages(before_id=)` + `delete_messages_from` 边界对齐 |
| H3 | 流式 text-start 清空已有缓冲(数据丢失) | store.ts 保留 `priorText = 既有 buf.text ?? 既有 msg body` |
| H4 | 非法 `kind`(unhashable/非法值)进入 set 校验崩溃 | `create_message` 标量 kind guard(`isinstance str and in _VALID_MSG_KINDS`) |
| M1 | rewind 删除边界用裸 `created_at>=` 漏/多删同刻行 | 复合边界 `or_(created_at>cutoff, and_(created_at==cutoff, id>=target.id))` |
| M2 | append 非幂等(重复 msg_id 产生重复行) | `upsert_message` 走 append_message 幂等路径 + `in_reply_to` 透传 |
| M4 | JSON 序列化非 UTF-8(中文转义) | `json_serializer=json.dumps(ensure_ascii=False)` 注入两个 engine |
| M3 | CJK 半角括号粗体 `**说明(重要)**` 不渲染 `<strong>` | **记为廉价不可修**(micromark CJK flanking;需 remark-cjk-friendly,已按 lockfile 主动排除)。2 个 unicode 测试**故意留红**作诚实文档 |

并新增不变量探针 `scripts/testkit/check_invariants.py`(INV1-9 + INV12 冲突终态)。

## 二、IA 改造(用户采纳我的锐评 → 落地)

**核心判断:** 把"联系人 vs 项目"二元割裂 → "一堆对话,部分挂着工作区"。数据模型本就支持(`Conversation.workspace_id: ULID|None`),所以改造主要在**端点 + 前端**,不是模型重写。

> **对抗测试又抓到一只:并发 promote 竞态。** `test_concurrent_promote_*` 用 `asyncio.gather` 同时升级同一对话 → check-then-act 跨 await,两协程都过守卫、各铸一个 workspace(孤儿项目)。复跑 8 次 6 红 2 绿(flaky=真竞态)。修复:per-event-loop 锁 `_promote_lock()`(镜像 BUG#1 `_pending_decide_lock`)包住 check→mint→attach→commit;复跑 10 次全绿。

### 后端 Phase 1(additive,16 测试)
- `PATCH /api/conversations/{id}/workspace` — 挂/卸工作区(`workspace_id: str|null`),附 system 事件;**不动 members/orchestrator**(群不变量保留)。
- `POST /api/conversations/{id}/promote` — 从对话铸造项目(auto sandbox)并挂载;已挂则 409。
- `GET /api/agents/{id}/conversations` — 统一"我和 X 的所有对话"(DM+群,跨项目);引号化 JSON 成员过滤(防 `agentX` 误命中 `agentXY`)。
- repo:`set_workspace_id` + `list_conversations(member=)`。
- 测试:`tests/api/test_ia_workspace_attach.py`(404/挂卸往返/群不变量保留/promote 409/子串安全/归档过滤)。

### 前端
- `api.ts`:`setConvWorkspace` / `promoteConvToProject` / `agentConversations`。
- **`NewConvModal` 泛化 `workspace: Workspace|null`**:null=独立对话,成员取全局联系人,`workspace_id` 留空。项目内调用方一字未改(行为不变)。
- **`Sidebar` Layer-1 新增「新建对话」入口** → 打开 `workspace=null` 的 modal。**至此"群聊不再以项目为前置"已端到端打通**。独立对话经 Inbox/搜索可达(`polynoia:resync-lists`)。
- 测试:`NewConvModal.projectless.test.tsx`(3 个渲染分支测试)。

### 前端 UI 三件套(2026-06-12 续,已 Playwright 实测)
对照用户的 `docs/conversation-first-ia-mock.html` 做了词汇/视觉对齐(绑定项目 / 新建项目并绑定 / 解绑项目 + 头部 scope 行)。
1. **对话头 ⋮「工作区与项目」菜单**(新 `ConvWorkspaceMenu.tsx`,挂在 `ChatPane` 头部,仅真实 conv 行):无项目→「绑定已有项目」(选择器 modal)+「新建项目并绑定」(promote);有项目→「解绑项目」。成功后广播 `polynoia:conv-updated` + `polynoia:resync-lists`,新项目推进 store。
2. **群组 scope 行**(ChatPane 头部):群聊副标题显示 `私有群聊 · 未绑定项目`(灰点)/ `已绑定项目 · 写入 worktree`(绿点)。
3. **`AgentDetailView` 新增「与 ta 的所有对话」**:`api.agentConversations(agentId)`,点行→派发 `polynoia:select-conv`(App 监听→`openConvAndSwitchToChat`,镜像既有 `edit-conv-roles`);标「当前」高亮。
4. **`Sidebar` Layer-1「群聊」专区**:列 `workspace_id===null && !direct` 的独立群聊,复用 SectionHeader,header「+」开 `新建对话`。

**Playwright 实测(:7788,真实点击)全过:**
- 建 2 联系人(阿核/数擎)→「新建对话」群聊页(项目外、全局花名册、scope 行)→ 选员+指定协调者 → 建出独立群聊「状态页上线讨论」,头部显示 `3 成员 · 私有群聊 · 未绑定项目`,侧栏「群聊」区 +1。
- ⋮ →「新建项目并绑定」→ promote 成功:聊天流出现系统卡「🚀 本对话已升级为项目…」,侧栏该会话从「群聊」移到「协作项目」;⋮ 翻为「解绑项目」;再「解绑项目」→ 移回「群聊」、项目保留。
- 阿核抽屉「与 ta 的所有对话 · 1」列出该群并标「当前」。

### 用户反馈迭代(2026-06-12 续二,Playwright 实测)
1. **「升级成项目」用户不喜欢 → 砍掉**:`ConvWorkspaceMenu` 现只剩「绑定项目」(复用现有 workspace)+「解绑项目」;promote/mint-new 入口移除。
2. **新建对话可复用现有工作区**:`NewConvModal`(项目外模式)新增「绑定项目」下拉(不绑定·私有对话 / 选现有项目),建会话即写入该 `workspace_id`。
3. **侧边栏统一为「所有会话」**:Layer-1 主区改成一条扁平会话流(DM / 群聊 / 项目内,行副标题 `单聊` / `群聊 · 私有对话 · N Agent` / `群聊 · <项目> · N Agent`,代表头像取 orchestrator/对方),对照 mock;「联系人」「协作项目」降为下方默认折叠的管理区。
4. **「输入框不见了」**:当前代码 composer 渲染正常(Playwright 实测 textarea 可见、`onScreen:true`、0 console error)——判定为用户那侧 HMR/桌面 dist 旧包,硬刷即恢复。
- 实测:建「状态页上线讨论」群聊(私有)→ ⋮「绑定项目」选「t」→ 头部 scope 翻「已绑定项目·写入 worktree」、聊天流出「📎 已挂载工作区「t」」、「所有会话」行变「群聊 · t · 2 Agent」。门禁:tsc 干净 / vitest 173 过(2 个 M3 留红)/ build OK。

### 用户反馈迭代(2026-06-12 续三:拉平 + 淡化项目 + 自选路径 + 边界修复)
- **整体拉平 + 淡化项目**:侧栏 Layer-1 改成单条「所有会话」流(取代 联系人/群聊/项目 三段);项目无侧栏主入口、列表里只留工作区色小点(hover 看名)、头部 scope 行用「已接入工作区 · 写入沙箱 / 私有群聊」措辞。
- **0-Agent 群聊边界 bug**(用户截图发现):后端 `create_conversation_endpoint` 拒绝无 agent 成员的会话(400 + 测试),前端「所有会话」过滤 0-agent 退化行,删掉历史 junk「t」。
- **工作区不可解绑 + 绑定只在创建时**:移除对话头 ⋮ `ConvWorkspaceMenu`(连带修掉它下拉面板渲染在白色头部里、撑高头部的布局 bug);绑定改为创建对话时在「新建对话」弹窗里选(私有 / 接入现有 / `+ 新建工作区…` 内联建,可填**已有项目绝对路径**)。`resolveWorkspaceId(memberIds)` 在建会话前按需 `createWorkspace` 再绑定。
- **自选工作区路径**:`Workspace.path` 整套机制本就接好(`workspace_root_for`/`_ensure_workspace_git` 的 adopt-real-repo / custom-init / 启动重注册),只差暴露:`create_workspace` 收 `path`(校验绝对+存在目录)+ 立即 `register_workspace_location`;**`delete_workspace` 加守卫——只 rmtree sandbox_root 子树,绝不删用户真实目录**(live 实测:删两个指向 `/tmp/my-real-project` 的工作区后该目录+`.git`+README 完好)。测试 `tests/api/test_workspace_custom_path.py`(5 个,含删除安全)。
- 全程 Playwright 实测:新建对话→`+新建工作区`填真实路径→建 DM→会话绑定到该路径工作区(`/private/tmp/my-real-project`)。门禁:后端 506 passed(1 known keychain)、前端 tsc 干净 / 173 passed(2 M3 红)/ build OK。

### 用户反馈迭代(续四:列表稳定排序 + 每行 ⋮ 操作菜单)
- **点会话不再跳到第一位**:会话列表排序兜底键 `updated_at` → `created_at`(`updated_at` 在已读/草稿/改角色时都会 bump,导致一点就窜顶)。现在只有新消息(`last_message_at`)才上浮。
- **每个会话行加 ⋮「会话操作」菜单**(新 `ConvActionsMenu.tsx`):重命名 / (群聊)成员与角色 / 归档 / 删除会话。下拉用 **createPortal + fixed 定位**(从按钮 rect 算位置),浮在最上层、绝不撑高/挤动侧栏(吸取上次 ⋮ 渲染进白底板的教训)。重命名走新后端 `PATCH /api/conversations/{id}/title`(`set_title` repo + 校验 + 测试);删除/归档复用既有 `deleteConv`/`archiveConv` + 广播 `polynoia:conv-deleted/archived/updated`+`resync-lists`。会话行从单 `<button>` 重构成 `div > button + ⋮`(避免按钮嵌套)。Playwright 实测:重命名生效、⋮ 删除生效、菜单浮层不挤布局。
- 观察到一个**潜在 DM 去重问题**(留作后续):扁平 IA 下发起单聊只走「新建对话」单聊页,每次都 `createConversation` 新建,对同一联系人点两次会产生重复 DM(老的 `dm-<id>` 合成路径会去重)。本次未改。

### 全量打磨 + 深测战役(续五,「全都修」+「改完再深度测」)
**14 项审计结论全部修复**:联系人编辑/删除→Agent 抽屉;工作区设置/删除→对话头 scope 行可点(NewProjectModal +onDelete);Layer-1 归档入口;死状态清理;DM 去重(已有同绑定单聊直接打开);`__new__` 工作区铸造后自动选中(防重复);⋮ 触屏/键盘可见(`@media(hover:none)` + focus-visible);重命名即时同步打开中的标题(`polynoia:conv-renamed`→App);行加时间戳/[草稿]/运行中绿点/99+/置顶分隔线/focus ring;移动端行加「顶」chip + ⋮ 菜单;`ConfirmDialog` 替代 window.confirm(删除/归档);⋮ 菜单与确认框 i18n(zh/en);refreshAllConvs 80ms 防抖;agentById Map。重命名输入框 onFocus 全选(深测发现打字会拼接旧名)。

**深测战役结果**:
- Playwright 全按钮:归档闭环(确认框→离开列表→归档视图→恢复)✓;重命名(真实键击+Enter)✓;scope 行→项目设置(预填名+删除按钮)✓;抽屉编辑/删除联系人 ✓;DM 去重(5→5)✓。
- 压力:120 会话渲染流畅、99+ 徽章 ✓、置顶跳顶+分隔线 ✓、搜索 125→10 ✓。
- **真实 agent 长上下文**:数擎(claude-haiku-4-5 真实 CLI)记两个事实 → 灌 5000 字文档 → 准确召回 BLUE-FALCON-42/5433 ✓。
- **跨聊天问答:抓到两个真 bug 并修复**——
  1. `remember` 把 `author_agent_id` 记成适配器静态 id(claudeCode)而非联系人 ULID(`ctx.agent_id` vs 惯用的 `ctx.turn_agent_id`,present/dispatch/ask 都用后者,唯独 remember 漏了)→ ADR-019 `list_agent_memory` 按联系人 id 过滤,跨会话召回永远空。修:`author = ctx.turn_agent_id or ctx.agent_id`。
  2. `build_shared_memory_layer` 群聊分支只读本群共享板,agent 自己其他会话的记忆(产品承诺的"单聊里说过群里也记得")根本不注入(ADR-019 只接了项目外 DM)。修:群/项目分支追加「你自己的工作记忆(来自你的其他会话)」段(仅 self-authored、排除本会话、headline 折叠、cap 15 —— 不泄露队友/项目细节,R1 立场不变)。
  - 修后实测:群里 @数擎 准确答出单聊里的两个值 ✓。回归测试 `tests/context/test_shared_memory_crossconv.py`(3 个,含不泄露他人记忆 + 本会话去重)。
- 门禁:后端 **510 passed**(1 known keychain),前端 tsc 干净 / 173 passed(2 M3 留红)/ build OK。

### 双端实测(续六:Tauri 桌面 + iOS 模拟器)
- **桌面 Tauri**:`cargo run --no-default-features`(src-tauri 下直跑 DevCommand,绕开 beforeDevCommand 跟已起的 vite 撞 7788)。窗口起,截图验证:扁平「所有会话」、20 用例、[草稿]/工作区点/时间戳,**置顶排序正确**(置顶行带图钉排第一)。
- **iOS 模拟器**(iPhone 17 Pro):`cap sync ios` → `xcodebuild` → `simctl install/launch`;连接门用 `simctl spawn <udid> defaults write com.polynoia.mobile CapacitorStorage.polynoia-server-url` 注入后直达首页。截图验证:20 用例、工作区 chip、时间戳、**每行 ⋮ 触屏可见**、「群」「顶」chip。
- **发现并修复**:移动端列表排序不前置置顶(recent/unread 模式)→ `pinRank` 加到两种模式的首键(「名称」模式保持纯 A→Z),重建+重装模拟器实测置顶行登顶 ✓。
- 模拟器注入的两个坑(已记入 memory):`CapacitorStorage` 是 key 前缀不是独立 domain;host 直写容器 plist 会被 sim 的 cfprefsd 缓存忽略,必须 `simctl spawn defaults write`。

> **环境发现(重要):** launchd 任务 `local.polynoia.server`(plist 在 `.tmp/testkit/launchd/server.plist`)起的后端**没带 `--reload`**(与 Makefile 的 `make server` 不同),所以改了后端 Python 不会自动加载 —— 本次靠 `launchctl kickstart -k gui/$(id -u)/local.polynoia.server` 重启才生效。本地开发可在该 plist 的 uvicorn 参数里加 `--reload`,或每次后端改动后手动 kickstart。

## 三、门禁结果

- **后端**:`uv run pytest -q` → **500 passed**, 1 failed(`test_refresh_credentials` — macOS keychain 缺源凭据目录的环境性失败,非回归), 5 skipped。
- **前端**:`tsc -b` clean;`vitest run` → **173 passed**, 2 failed(M3 CJK 故意留红);`vite build` OK。

## 四、留待用户视觉验收(我无法看 UI / computer_use 未授权)

后端 + client 已就绪,只差"看得见像素"的接线:
1. 侧栏给**独立(无工作区)对话**一个专属 section(目前仅 Inbox/搜索可见,不够显眼)。
2. 对话头 ⋮ 菜单加「挂工作区 / 升级为项目」按钮(`setConvWorkspace`/`promoteConvToProject` 已备)。
3. `AgentDetailView` 加"与 X 的所有对话"抽屉(`agentConversations` 已备)。

> 这些是导航重排,涉及视觉布局,适合用户在场边看边接;盲改侧栏导航风险高,故止于此。

## 2026-06-13 续:质量体系 + 生态集成(事件日志/质量画像/角色库/流水线/测评集)

- **turn_events 事件日志**:追加式表 + `emit()` 单点同步旁路(缓冲+1s 批量+delta 合并,不动承重时序);`GET /conversations/{id}/events`。浸泡实测 89 事件 seq 无空洞。
- **质量画像**:`GET /api/quality` 综合分=基准45%+工具25%+进程20%+活跃10%(无证据组件记中性 0.6);侧栏联系人分数 chip + 质量面板(📊)。
- **角色预设库**:agency-agents(232 角色,MIT)同步/浏览/搜索/雇佣 → 真实联系人(frontmatter→tagline/色,正文→system_prompt)。
- **项目流水线**:gstack 七阶段 SOP 模板;槽位复用既有联系人/缺口现雇;群聊带 orchestrator + SOP 草稿。
- **测评集沉淀**(docs/testing/benchmark-suite.md):benchmarks.py(20 用例验收,6 个专项)+ run_benchmark.py(WS 真实轮次驱动+双重沉降判定)+ soak.py(5 类不变量)。
- **首批基线**:game_2048 × deepseek-v4-flash-free(免费弱模型)PASSED 100%(9/9);× glm-5.1 PASSED 100%;浸泡 3 轮 7 不变量全过。两个弱模型综合分 84 登顶——弱模型论证首组数据。
- 门禁:后端 528 passed(+15 新测试)/前端 tsc+build 绿。坑:Sidebar 双分支(collapsed/expanded)插 UI 必须认准渲染分支;目录搜索需 name 精确匹配优先。
