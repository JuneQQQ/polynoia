# Polynoia 冲突闭环设计(Conflict Closed-Loop)

> **状态**:proposed(待 review → 落 ADR-017 + 实施)
> **日期**:2026-05-30
> **分支**:`feature/diff_dev`
> **取代**:`docs/design/diff-sandbox-mcp-2026-05-27.md` 中关于"冲突处理"的部分(旧文是 P0 sandbox/MCP 样式记录,非冲突方案)
> **理论基线**:`/root/AgentHub/RESEARCH_方法迭代_深研版.md`(v40,130+ 来源)+ `ULTIMATE_多Agent协作防冲突_终极方案.md`(v23)+ `CODE_REVIEW_diff链路.md`(POC 避坑)
> **代码地基**:由 `conflict-closed-loop-design` workflow(8 路 ground-truth + 3 设计 + 评分 + 对抗复审,git 2.25.1 实测验证)产出
> **关联**:ADR-003(workspace-shared git)· ADR-005(merge mode)· ADR-009(manual long-poll)· ADR-013/014/015(角色化工具 + 共享记忆 + 闭环协作)· ADR-016(CodeMirror + @git-diff-view)

---

## 0. TL;DR

把今天"多 Agent 并行分支 → 合并撞冲突 → `git merge --abort` → 标 `needs-manual` → 无人接手"的**静默死胡同**,补成一条**可见、可解、扛刷新**的闭环:

> 真实 git 探测冲突(不留半合并)→ 冻成一等 `conflict` typed card + `ConflictRow`(单一事实源)→ 在 IM 流里对所有人可见 → LLM 自动修 **或** 用户在 `@git-diff-view` 手动解 → **再真合并一次** → 同卡翻 `resolved` + 系统卡确认。

一句话哲学(承袭深研版 v40):**结构冲突用确定性手段消灭到零误报;语义/意图冲突用契约+测试暴露、交人裁决、可廉价回滚;协调成本只按真实写争用付费。**

---

## 1. 问题与范围

### 1.1 现状(代码核实)

群聊 + workspace 模式下,每个 `(agent, conv)` 在独立分支 `agent/{id}/conv-{id}` + worktree 上干活(ADR-003)。一个 burst 的所有 worker 完成后,**真实合并路径**是:

- `api/routes.py:_merge_burst_to_main`(1683)在 `_mark_burst_task`(1599)的 `is_last` 分支(1630)被触发
- 先 `commit_pending_worktrees`(1699)扫各 worktree 提交未 commit 的改动(收 OpenCode 原生写)
- 再逐分支 `sandbox/_core.py:merge_branch_into_main`(542):`git checkout main` + `git merge --no-ff`,**任何非零 rc → `git merge --abort`(568)→ 丢弃 + 返回 `(False,'','conflict: ...')`**
- 整段裹在 try/except(1629),合并失败被吞,**用户什么都看不到**

> ⚠️ **关键地基纠正**:`orchestrator/runtime.py` 的 `OrchestratorRuntime._maybe_run_merge_phase` / `_detect_conflicts` 是**死代码**(只在测试 import,`run_turn` 生产从不调用;routes.py 有注释明说 deliberately bypass legacy)。**冲突闭环必须挂在 `_merge_burst_to_main` 上**,启发式 `_detect_conflicts`(同文件多 writer)**不复用**——我们用真 git 冲突检测取代它。

### 1.2 缺口的代价

`rule.md` 考核项「代码冲突处理」直接给分;spec §2.3、ADR-003 §合并策略、ADR-005 都把"LLM 驱动冲突解决 / 手动冲突 UI"标为 P2/follow-up。这是 diff 分支 ROI 最高、最可演示的一刀。

### 1.3 范围

**本设计覆盖(IN)**:真实结构冲突的检测、冻结、可见化、手动解决、LLM 自动解决、再合并确认。

**明确不覆盖(OUT,见 §9 诚实边界)**:
- 语义冲突的**完备**检测(write skew 定理级漏检 —— 见 §2)。本设计只验证"标记已消除 + 合并能 conclude",不验证编译/测试通过(P2+ 接 Bayou 式受影响测试)。
- 意图冲突 —— 已由 ADR-014/015 的 dispatch contract + conv_memory(共享规格底座)在**动手之前**处理,不在合并期解决。
- select-and-describe 编辑、版本 checkpoint/restore —— 属"编辑体验"另一刀,不在本设计。

---

## 2. 理论依据(为什么这样设计,不是拍脑袋)

承袭 `/root/AgentHub` 的 v40 深研(130+ 来源)三条铁律,它们决定了本设计的形状:

**铁律一:结构冲突 = 已解决问题,别再发明检测器。**
OCC 写集 + 3-way merge + 一等冲突可做到零漏报零误报。→ 本设计**直接用 git 自己的合并引擎**做检测(`git merge --no-commit`),不自建正则/AST 冲突探测(POC 的 `semantic_check.py` 自建语义闸是 H4 误报重灾区,**已退役不复用**)。

**铁律二:语义冲突 = 定理级漏检(write skew)。** 写集比较机制性漏掉"A 删函数 / B 新增调用"。唯一务实解是 Bayou 式——可执行断言 = 重跑受影响测试。→ 本设计**诚实承认覆盖缺口**:P1/P2 只保证文本层合并成功,语义验证推 P2+(与现有 report-based 闭环验证一致,ADR-015)。

**铁律三:协调只在"非单调"操作上需要(CALM 定理)。** 纯新增(新文件/新符号/append)= 单调 = 可交换 = 免冲突直接合;只有改/删/重命名共享符号才撞冲突。→ 本设计天然受益:burst 产物大多是新文件(免冲突,走今天的 clean 路径),**重武器只花在真冲突的那一小撮分支上**。

**借鉴 jj 一等冲突 + git rerere:** 冲突不是阻塞态,是**可挂起、可延迟、可裁决的一等数据**。→ 本设计把冲突冻成 `ConflictRow` + card,四态机 `open → resolving → resolved | abandoned`。

**借鉴 Tricorder 10% 误报红线:** 启发式信号只能折叠提示、绝不阻塞。→ LLM 自动解冲突只当"尝试一次"的辅助,失败回落人工,不阻断;真 git 冲突(确定性)才是 source of truth。

**映射深研版"六阶段漏斗"——本设计落的是阶段 4–6:**

```
[0 可并行性判定] [1 避免/单调性] [2 隔离 worktree]  ← 已由 ADR-003 + dispatch 落地
[3 合法性前置门]                                    ← 已由 MCP 工具沙箱 + role 落地
[4 提交校验=结构层]  真 git 3-way 合并,冲突=一等数据  ← 本设计核心
[5 单一裁决面]      conflict card + 解决 pane,交人/LLM  ← 本设计
[6 恢复+学习]       git revert 可回滚 + provenance 入 conv_memory  ← 本设计(P2)
```

---

## 3. 当前地基(LIVE code,workflow 实测)

复用这些既有"轨道",新代码大量是它们的镜像,**低风险、surgical**:

| 既有机制 | 位置 | 复用方式 |
|---|---|---|
| 真实合并触发 | `routes.py:_merge_burst_to_main` (1683) | **唯一**改动点:`merge_branch_into_main` → `probe_merge` |
| workspace 合并基建 | `_core.py` `_workspace_run`(574,cwd=workspace root)/ `list_agent_branches`(419)/ `branch_ahead_of_main`(499)/ `commit_pending_worktrees`(447) | 新 `probe_merge`/`conclude_merge` 同样走 `_workspace_run` |
| pending-edit 全套 | create(1068)/ wait 长轮询(1106)/ decide(1131)/ list(1161)/ `_gate_via_pending_edit`(tools.py:40) | conflict 的 row + 长轮询 + decide + hydrate **逐一镜像** |
| WS 广播 | `routes.py:_broadcast_to_conv`(166)→ 所有 `_conv_outboxes` | conflict card 直接走它,多 tab + 刷新自动同步 |
| `data-*` 卡自动入流 | `ChatPane.tsx`(~146 `startsWith('data-')`) | `data-conflict` 卡零额外接线即渲染 |
| 同 id 重发原地更新 | tasks burst card(1617) | conflict card 同 `message_id` 重发翻状态 |
| 共享记忆 | `add_conv_memory` + ConvMemoryRow + memory 端点 | provenance(检测/解决)写入,orchestrator 收尾读回 |
| HTTP-callback 轨 | `tools.py:_callback_server`(811-840),dispatch/remember/recall 用 | LLM 的 `resolve_conflict` MCP 工具走它 |
| 角色化工具 | `ROLE_TOOLS`(tools.py:1080) | `resolve_conflict` 加给 coder/generalist/orchestrator |
| diff 渲染 | `@git-diff-view`(ADR-016,**无真 3-way 模式**) | Split 模式喂 ours vs theirs |

**不可违背的硬约束(workspace 共享 HEAD):** workspace root 跨所有 worktree + 所有 conv **只有一个 HEAD/index**。合并只能在 root 跑(`git checkout main` 在 root,worktree 各自钉着自己分支)。**绝不能把 root 留在半合并态** —— 这是整个设计的正确性命门,也是 winner 方案胜出的唯一决定轴。

---

## 4. 设计:Real Sequential Merge + Typed conflict Card

### 4.1 状态机

```
                       ┌─────────────── 同一张 card(stable message_id)───────────────┐
  burst 全员完成        │                                                              │
      │                ▼                                                              │
 probe_merge ──clean──→ (直接 commit,发"✓ 合并"卡)        [无冲突分支:今天的路径]
      │
      └──conflict──→ [open] ──(auto: LLM 修)/(manual: 等人)──→ [resolving] ──┬─成功─→ [resolved]  → 系统卡 "X 解决了 → main@sha"
                        │                                                    └─放弃─→ [abandoned] → 系统卡 "分支未合并"
                        └────────────── 扛刷新/多 tab(ConflictRow + 重发卡)──────────────┘
```

`merge_mode`(auto/manual)**只决定"谁先解"**(auto 先让 LLM 试一次,manual 直接等人),数据路径完全一致。

### 4.2 git 机制(全部在 workspace root via `_workspace_run`,git 2.25.1 实测)

> git 2.25.1 **没有** `git merge-tree --write-tree`(只有 legacy 3-arg 形式,输出不可机读)。所以用**真合并引擎探测**,而非 plumbing。已实测验证。

**探测 `Sandbox.probe_merge(branch) -> (status, detail)`**(在 `_merge_burst_to_main` 逐分支调用):

```
0. [守卫] 若 root 有 stray MERGE_HEAD 或 dirty(见 §5.2)→ 先清理
1. git checkout main
2. git -c merge.conflictStyle=diff3 merge --no-commit --no-ff -m "polynoia: merge <branch>" <branch>
   · rc==0 且无 U 文件 → CLEAN:git commit --no-edit(MERGE_MSG 已预填)→ 同今天 success
   · rc!=0 → CONFLICT:
       a. 对每个 `git diff --name-only --diff-filter=U` 的 path,按类型(§7)抓:
          - markers = 读 worktree 文件(含 <<<<<<< ||||||| ======= >>>>>>>)
          - base   = git show :1:<path>   ※ rc!=0(add/add 无基)→ base=None,绝不把 stderr 当内容
          - ours   = git show :2:<path>   (main 侧;某些类型缺失)
          - theirs = git show :3:<path>   (branch 侧;某些类型缺失)
       b. git merge --abort   ← 实测:root 回到 status --porcelain 空 + 无 MERGE_HEAD(干净!)
3. 返回 (clean|conflict, {files:[...], ...})  —— 冲突分支冻成 ConflictRow,不阻塞下一分支
```

**顺序语义**:保持今天的逐分支顺序。分支 A 干净合并并 commit;分支 B 随后对"含 A 的 main"探测——这是**期望行为**,冲突针对真实 main 状态报告。

**解决 `Sandbox.conclude_merge(branch, resolutions) -> (ok, sha, msg)`**(幂等,锁内执行):

```
0. [锁] 取 per-workspace 锁(§5.1)
1. git checkout main;校验 branch 仍存在且 branch_ahead_of_main>0(否则标 stale → 重新探测)
2. git -c merge.conflictStyle=diff3 merge --no-commit --no-ff <branch>   (重入,MERGE_HEAD 置位)
3. [重新快照] 此刻重读 :2:/:3: blobs(顺序合并可能已移动 main,冻结的 row blob 可能过期)
4. 逐 path 写入 resolutions[path] → git add <path>
5. 若仍有 U 文件 → 守卫式 git merge --abort,返回 partial(停在 resolving)
   否则 git commit --no-edit -m "polynoia: resolve+merge <branch> into main" → 取新 sha
6. [finally] 若 MERGE_HEAD 仍在(任何异常)→ 守卫式 abort,保证 root 永不半合并
```

### 4.3 数据模型(新 typed payload + 新表 —— P0 走 create_all,无 Alembic)

**`ConflictPayload`(`domain/messages.py`,加入 MessagePayload 判别 union,第 18 种 kind):**

```python
ConflictType = Literal["content", "add_add", "modify_delete", "rename", "binary"]

class ConflictFile(BaseModel):
    path: str
    ctype: ConflictType                 # ← 类型决定解决 UI(见 §7)
    markers: str | None                 # content 型才有;binary/modify_delete 为 None
    ours: str | None                    # :2: blob(modify_delete 一侧缺失)
    theirs: str | None                  # :3: blob
    base: str | None                    # :1: blob(add_add 为 None)
    is_binary: bool = False
    resolution: str | None = None       # 最终解决内容(content 型);take-side 用枚举
    side: Literal["ours", "theirs", "delete"] | None = None  # 非 content 型的选择
    state: Literal["conflict", "resolved"] = "conflict"

class ConflictPayload(BaseModel):
    kind: Literal["conflict"] = "conflict"
    conflict_id: ULID                   # FK → ConflictRow
    conv_id: ULID
    branch: str                         # agent/<id>/conv-<id>
    agent_id: str                       # branch.split('/')[1],用于 per-agent 着色/归属
    into: str = "main"
    status: Literal["open", "resolving", "resolved", "abandoned"]
    files: list[ConflictFile]           # ← 卡内 blob 受 64KB/文件 cap(§5.4)
    resolved_by: str | None = None      # agent_id 或 "you"
    resolved_sha: str | None = None
    created_at: datetime
    decided_at: datetime | None = None
```

> `make types` 自动生成 TS(**永不手写** packages/shared,CLAUDE.md §6.1)。`append_message` 存 raw dict 无 Pydantic 校验(repo.py:515/525),`list_messages` 返 raw(639)→ 新 kind 不破坏服务端 timeline hydration,但**仍必须**进 union 否则 TS 类型手写。

**`ConflictRow`(`storage/models.py`,全新表 → `Base.metadata.create_all` 自动建,无需 `_SCHEMA_PATCHES`):**

```
id(ULID PK) · conv_id(FK CASCADE, indexed) · workspace_id(indexed)
branch(Text) · agent_id(str64) · into(str16='main')
status(str16 indexed, default 'open')
files_json(JSON: 完整 ConflictFile dict 列表,含未裁剪 blob = 单一事实源)
resolved_by(str64?) · resolved_sha(str40?) · card_msg_id(str26 = 稳定卡 id)
created_at · decided_at?
```

`repo.py` 加:`create_conflict / get_conflict / list_conflicts(conv_id, status?) / set_conflict_status / update_conflict_files`(pending-edit repo 的直接镜像)。`_conflict_to_dict` 镜像 `_pending_edit_to_dict`。

### 4.4 后端改动

1. `_core.py`:新 `probe_merge`、`conclude_merge`(§4.2),均 `_workspace_run` 薄包装。**保留** `merge_branch_into_main` 原样(死的 orchestrator caller 不动,不破坏 `test_orchestrator_designation`)。
2. `routes.py:_merge_burst_to_main`:把盲 `merge_branch_into_main(b)` 换成 `probe_merge(b)`。clean → commit + 发"✓ 合并"卡(graft,见 §4.7);conflict → `create_conflict(status='open')` + 发 `data-conflict` 卡 + 一行系统 text 卡(IM 不变量)+ 写 conv_memory(graft)。**需补**:`get_conversation(conv_id).merge_mode` 查询(reg 不带 merge_mode),auto 模式立即 spawn LLM 修复轮。
3. `routes.py` 新端点(镜像 pending-edit):
   - `GET /api/conversations/{conv_id}/conflicts`(刷新 hydrate)
   - `GET /api/conflicts/{id}`(取**完整未裁剪** blobs,给 resolve pane)
   - `GET /api/conflicts/{id}/wait`(长轮询,复用 500ms/120s,给 MCP/LLM 阻塞等)
   - `POST /api/conflicts/{id}/resolve` `{resolutions:{path:content}, resolved_by}` → `conclude_merge` → 翻 row → 重发同卡 + 系统卡 "X 解决了 → main@sha"
   - `POST /api/conflicts/{id}/abandon` → status='abandoned' + 卡 + 系统卡(绝不静默)
4. **per-workspace 锁**(§5.1)+ **崩溃恢复 guard**(§5.2)—— 见关键正确性约束。

### 4.5 前端改动

1. `lib/types.ts`:`ConflictPayload`/`ConflictFile` 由 `make types` 生成。
2. `components/parts/ConflictPart.tsx`:卡片(open/resolving/resolved/abandoned 摘要 + branch + agent badge + N files + 状态 pill);open/resolving 显"解决冲突"按钮开 pane。注册进 `parts/index.tsx` PARTS_REGISTRY key `conflict`(唯一注册改动,`data-conflict` 已自动入流)。
3. `components/preview/ConflictResolvePane.tsx`:新 pane,与 `DiffReviewPane` 同槽(PreviewPane)。按文件类型(§7)分派:`content` → `@git-diff-view` Split(ours vs theirs)+ CodeMirror 编辑去标记;`add_add`/`modify_delete`/`rename` → take-ours/take-theirs/delete 单选;`binary` → 仅 take-side。"Resolve" POST `/resolve`。
4. `store.ts`:`conflictsByConv: Map` + `upsertConflict` + `hydrateConflicts`(pendingEditsByConv 直接拷)。
5. `ChatPane.tsx`:`data-conflict` 分支路由到 `upsertConflict`(驱动 pane + 扛刷新);conv-switch 时 `GET /conflicts` hydrate(镜像 pending-edit ~178)。
6. `lib/api.ts`:`resolveConflict / abandonConflict / listConflicts / getConflict`。

### 4.6 LLM 自动解决流(P2,复用 `_spawn_turn` + `run_adapter_turn`,无新子系统)

auto 模式探测到冲突 → row 翻 `resolving` → 在**分支作者 agent**(保留领域上下文)上 spawn 一个聚焦修复轮。prompt 由 row 构建:冲突文件路径 + `markers` blob 内联 + 锁定的 contract(recall conv_memory),指令:「你在 `<branch>` 的改动与 main 上 `<other>` 的改动冲突。下面是带标记的文件。给出合并后最终内容(保留双方合法意图,删掉 `<<<<<<< ======= >>>>>>>` 标记),完成后调用 `resolve_conflict` 工具提交。」

新 MCP 工具 `resolve_conflict(conflict_id, files:[{path,content}])`(加进 coder/generalist/orchestrator 的 `ROLE_TOOLS`),`execute()` 经 `_callback_server` POST `/api/conflicts/{id}/resolve`(同 dispatch/remember 轨)。**ToolContext 有 conv_id 但无 conflict_id**,需经 prompt/工具参数传入。

**单次上限**(`files_json` 里记 retry 或简单 guard,对齐 `_MAX_MENTION_CHAIN_DEPTH` 纪律):LLM 失败/残留标记 → `conclude_merge` abort + 返 partial → 卡停 `resolving` 注"LLM 未能解决,等待人工",manual 流接管同一行。修复轮 fire-and-forget(同 1629 try/except),不阻塞 burst 收尾。

### 4.7 grafts(从 CCT/CAP-E 折入 winner)

- **干净合并也发卡(CCT,最大答辩/课题收益)**:今天 `_merge_burst_to_main` 静默(1687)。让**每次** burst 合并都发"✓ branch → sha · ⚠ branch 冲突"结果卡(stable id + 冲突时同卡翻状态),IM 不变量与 ADR-005 result-card spec 才真正闭合。
- **conv_memory provenance(CCT)**:检测写 `kind='conflict'`、解决写 `kind='decision'/'artifact'`,orchestrator 现有收尾轮(1671)读回并在 wrap-up 提及。零新模型,强化闭环 + AI 协作记录(CLAUDE.md §9)。
- **大文件 cap(CAP-E)**:卡 payload 内 blob 单文件 cap 64KB + fallback flag;完整 blob 留 ConflictRow(单一事实源),resolve pane 经 `GET /api/conflicts/{id}` 取完整内容,**绝不**用裁剪后的卡副本去 commit(否则大文件静默截断)。

---

## 5. 关键正确性约束(critic 实测必修项 —— 提升为一等需求,非"风险栏 TODO")

> 这些是 critic 在 git 2.25.1 上**实测**出的命门。P1 上线前必须落地,否则破坏共享 workspace。

### 5.1 per-workspace 锁(MANDATORY,correctness 非 polish)
多个 conv 可共享同一 `workspace_id`(conv create 时设,routes.py:564),`_merge_burst_to_main` 按 conv 独立触发,**今天无串行化**。两个 conv 并发 burst 都在同一 root `git checkout main` + merge → **实测会互相踩坏 / 留半合并**。
→ 新模块级 `_ws_merge_locks: dict[workspace_id, asyncio.Lock]`(镜像 `_conv_agent_locks`),把 **`commit_pending_worktrees` → `probe_merge`(整循环)→ `conclude_merge`** 包成一个临界区,**键为 workspace_id 而非 conv_id**。`conclude_merge` 的重入(可能远晚于 burst)也必须取同一锁。

### 5.2 崩溃恢复 guard(MANDATORY)
`_merge_burst_to_main` 顶部 + workspace-open 时:若 `git rev-parse -q --verify MERGE_HEAD` 成功 **或** `git status --porcelain` 非空 → 守卫式 `git merge --abort`(+ 必要时 `checkout -- . `)恢复干净可合并 main。
→ **实测坑**:`git merge --abort` 在无 MERGE_HEAD 时 rc=128('no merge to abort')—— **必须先 `rev-parse --verify MERGE_HEAD` 判断**,绝不盲调。且 `git checkout main` 在 root 被 dirty 时仍"成功"('Already on main' + M 文件),随后 `git merge` 才报错——所以守卫要**同时**查 MERGE_HEAD 和 dirty。

### 5.3 conclude 时重新快照(MANDATORY)
顺序合并会在 detect 与 resolve 之间移动 main。第二个冲突 row 冻结的 `:2:`(ours)blob 是**第一次解决之前**的——手动 pane 会显示过期 ours。→ `conclude_merge` 重入时**重新读** `:2:/:3:` blob,不信冻结 row;`branch_ahead_of_main` 再校验,stale → 标记重新探测。

### 5.4 部分解决持久化(MANDATORY)
`conclude_merge` 遇残留 U 文件会 abort——但 abort 丢掉用户**已输入**的那些文件的解决(只活在 request 里)。→ 解决内容先 `update_conflict_files` 持久化到 `files_json`,再尝试 conclude,partial 不丢。

### 5.5 接线细节(实测纠正)
- `Sandbox.open_workspace_if_exists` 是 **sync `@classmethod`**(_core.py:190),routes.py:1694 **不带 await**——新代码**绝不加 await**。`probe_merge`/`conclude_merge` 是该 sync 对象上的实例方法,正常。
- `_merge_burst_to_main` 的 `reg` **不带 merge_mode**(只有 payload/pending/orch/workspace_id/contract)→ 需额外 `get_conversation(conv_id).merge_mode` DB 查。
- `git init -b main`(_core.py:216)在 2.25.1 是**潜在 bug**(`-b` 不支持)——新代码**不得照抄**该 idiom(用 `git symbolic-ref HEAD refs/heads/main`)。预存问题,本设计不引入但勿沿袭。

---

## 6. 不复用的东西(避坑,来自 POC CODE_REVIEW)

POC(`/root/AgentHub/poc/`)的教训,本设计**有意规避**:

| POC 缺陷 | 本设计如何避免 |
|---|---|
| **C1** LOW/MEDIUM 直接写主仓库,Apply 闸门形同虚设 | polynoia 已用 per-agent worktree(ADR-003),改动落地在分支不在 main —— 架构上已修 |
| **C2** `_PENDING_DIFFS` 内存,重启全 404 | ConflictRow 持久化 + 卡可重发,扛刷新/重启(§5.2 还加崩溃恢复) |
| **C5** worktree commit 用户未提交改动 / 泄漏分支 | 不碰用户分支;合并只在 root 的 main 上;冲突 abort 干净 |
| **H4** 自建正则/单文件 mypy 语义闸,大量误报 | **不自建语义检测**,用 git 真冲突(确定性);语义验证推 P2+ 用项目自己的测试 |
| **M2** 黄被当红强制 | conflict 状态明确分 open/resolving/resolved/abandoned,不混淆 |
| **M5** 草稿决策未 Apply 也注入 context | 解决前 row 是 source of truth,resolved 才 commit 进 main |

---

## 7. 冲突类型分类表(critic 实测,P1 必须区分)

`git merge` 的冲突**不止"文本内容冲突"一种**——实测各类行为迥异,文本合并模型对多数类型**无效**:

| 类型 `ctype` | 检测特征(实测) | blob 状态 | 解决 UI |
|---|---|---|---|
| `content` | worktree 有 `<<<<<<<` 标记 | base/ours/theirs 齐(或 base=None) | `@git-diff-view` Split + CodeMirror 编辑去标记 |
| `add_add` | 双方都新增同名文件,无基 | **`git show :1:` rc=128**(非空!)→ base=None | ours/theirs 二选一 或 编辑 |
| `modify_delete` | status `DU`/`UD`,**worktree 无标记** | 一侧 blob 缺失(tombstone) | **keep-file / delete-file 单选**(文本合并无意义) |
| `rename` | rename/rename、rename/delete | 路径不一,blob 错位 | 选保留哪个路径/内容 |
| `binary` | `warning: Cannot merge binary files`,**无标记** | 二进制,**不可 UTF-8 decode** | 仅 take-ours/take-theirs;blob 存 base64 或跳过 |

> **必修**:`_workspace_run` 的 `.decode('utf-8','replace')` 会**损坏二进制**。binary 检测(NUL 字节 / `git diff --numstat` 显示 `-`)→ 不 decode 进 `markers:str`、限 take-side、blob base64。`modify_delete`/`rename` 的 LLM"删标记" prompt 会产出垃圾 —— 必须走 keep/delete 选择分支。

---

## 8. 分阶段实施(诚实 LOC)

| 阶段 | 交付 | 估算 |
|---|---|---|
| **P1 检测+冻结+可见** | `probe_merge` 替换盲合并;ConflictRow 表 + repo;ConflictPayload + `make types`;`data-conflict` 卡 + 系统卡 + 持久化;ConflictPart 注册;conflictsByConv store + hydrate;**per-workspace 锁 + 崩溃 guard + 冲突类型分类**。终态:冲突可见、扛刷新、main 永不半合并(仅 open,未解) | ~250 backend + ~120 frontend,**+ 安全件后 ~实际 backend 接近 350** |
| **P1b 手动解决** | `/resolve` + `/abandon` + `GET /conflicts/{id}`;`conclude_merge`(重新快照 + 部分持久化);ConflictResolvePane(按类型分派);api 助手;同卡翻状态 + 系统卡确认 | ~80 backend + ~180 frontend |
| **P2 LLM 自动解决** | `resolve_conflict` MCP 工具(HTTP-callback);auto 模式 spawn 修复轮;单次上限;失败回落人工(复用 P1b 的 pane) | ~90 |
| **P2+ 语义验证(可选)** | Bayou 式:解决后跑受影响测试/类型检查再确认 main | 另议 |

> **总计 backend ~520–550(含 binary/modify_delete + 锁 + 重新快照),frontend ~300。** 80% 是 pending-edit/burst-card 既有轨道的镜像,真正新 git 逻辑只有 `probe_merge` + `conclude_merge`(~120 LOC,已在 git 2.25.1 端到端验证)+ per-workspace 锁(net-new,load-bearing)。

---

## 9. 诚实边界(本设计"不"解决什么)

承袭深研版第五部分的诚实声明:

1. **不保证抓住所有语义冲突。** 文本合并成功 ≠ 语义正确(write skew 定理级漏检)。P1/P2 只验证"标记消除 + merge conclude",**不验证编译/测试通过**。语义验证推 P2+(Bayou 受影响测试),且有覆盖上限(动态语言尤甚)。
2. **意图冲突不在合并期解决。** 由 dispatch contract + conv_memory 在动手前传递共享规格(ADR-014/015);本设计只处理"已发生的结构冲突"。
3. **LLM 解冲突是辅助非保证。** 单次尝试,失败回落人工(Tricorder 纪律:启发式不阻塞)。
4. **顺序合并的 stale 窗口。** 后解决的 row 针对当前 main(§5.3 重新快照缓解),但若先合并的兄弟分支后被 revert,解决可能基于已变 main —— 可接受,P1 以当前 main 为准。

---

## 10. 测试矩阵

1. **clean 合并** → commit + "✓" 卡,main 前进,无 ConflictRow。
2. **content 冲突** → ConflictRow(open)+ 卡 + 系统卡;`git status` 空 + 无 MERGE_HEAD(probe 后干净)。
3. **手动解决** → `/resolve` → conclude_merge 产真 2-parent merge commit;卡翻 resolved;系统卡。
4. **add/add** → `git show :1:` rc=128 → base=None(不崩);ours/theirs 二选一可解。
5. **modify_delete** → 无标记,keep/delete 单选;LLM 不走文本合并分支。
6. **binary** → 不 decode 损坏;仅 take-side;blob base64。
7. **多分支双冲突** → 两 ConflictRow;解第一个后第二个 conclude 时**重新快照**(ours 不 stale)。
8. **扛刷新/多 tab** → 断连重连后 `GET /conflicts` hydrate;另一 tab 收状态翻转。
9. **per-workspace 并发** → 同 workspace 两 conv 并发 burst,锁串行,main 不损坏。
10. **崩溃恢复** → 留 stray MERGE_HEAD / dirty,下次 `_merge_burst_to_main` 顶部 guard 守卫式 abort 恢复。
11. **LLM 失败回落** → 残留标记 → conclude abort → 卡停 resolving → 人工接管同行。
12. **abandon** → branch 未合并 + 系统卡明示(非静默)。

---

## 11. 图示规范产物(CLAUDE.md §12 — GPT-IMAGE-2 prompt)

> 关键生命周期过程,按 §12.1 触发,归档于此。

```
A clean, technical infographic in modern flat-design style on a soft off-white
(#FAF8F4) background. Title at top in bold sans-serif:
"Polynoia · Merge Conflict Closed-Loop (probe → freeze → resolve → re-merge)".

Horizontal left-to-right flow in FIVE numbered stages, each a rounded rectangle
with a thin 1.5px stroke, connected by arrows. A per-workspace LOCK icon (a small
padlock, warm orange #F2994A) spans the whole strip with the label
"per-workspace asyncio.Lock — one HEAD/index shared across all worktrees & convs".

STAGE 1 — "PROBE (real merge, no residue)", soft blue #5B8FF9 header:
  box showing `git merge --no-commit --no-ff <branch>` at workspace ROOT,
  two outcomes: green arrow "clean → git commit → ✓ merge card" and
  red arrow "conflict → capture :1:/:2:/:3: blobs + diff3 markers → git merge --abort
  (root stays CLEAN, MERGE_HEAD cleared)".

STAGE 2 — "FREEZE (first-class data)", warm orange #F2994A header:
  a ConflictRow cylinder (source of truth, JSON files_json) + a `data-conflict`
  card (stable message_id). Five small type chips: content / add_add /
  modify_delete / rename / binary.

STAGE 3 — "SURFACE (everyone sees)", gray #E5E7EB header:
  an IM chat bubble with a conflict card "⚠ <branch> · N files · OPEN" +
  a system text line; a small multi-tab + refresh icon labeled
  "_broadcast_to_conv → all tabs · GET /conflicts hydrate".

STAGE 4 — "RESOLVE (two paths converge)", with a branch:
  upper lane (purple #8B5CF6) "LLM repair turn → resolve_conflict MCP tool"
  (auto mode, single attempt, fallback↓);
  lower lane (blue) "user @git-diff-view Split ours|theirs + CodeMirror"
  (manual mode). Both arrow into one endpoint "POST /api/conflicts/{id}/resolve".

STAGE 5 — "RE-MERGE FOR REAL", fresh green #27AE60 header:
  `conclude_merge`: re-enter merge, RE-SNAPSHOT blobs, write resolutions,
  git add, git commit (2-parent). Card flips → RESOLVED, system card
  "X 解决了 <branch> → main@<sha>". A finally-guard chip (red #E5484D):
  "rev-parse MERGE_HEAD → guarded abort (never leave root half-merged)".

BOTTOM RAIL — a thin strip titled "Honest boundaries": three gray boxes —
"structure = deterministic (git)", "semantic = probe via tests (P2+, has gaps)",
"intent = shared contract/ledger, pre-action (ADR-014/015)".

Color palette: off-white bg, soft blue #5B8FF9 = system/git, warm orange
#F2994A = tools/lock/markers, gray #E5E7EB = messages/UI, fresh green #27AE60 =
success/commit, red #E5484D = abort/guard, purple #8B5CF6 = LLM/orchestrator,
dark slate #1F2937 = text. Thin 1-2px strokes, no 3D, no shadows except title.
Monospace for all tokens (git merge --no-commit / MERGE_HEAD / ConflictRow /
probe_merge / conclude_merge / data-conflict / :2: / :3:).

Aspect ratio: 16:9.
```

归档:`docs/diagrams/conflict-closed-loop.md`(渲染图入 `pic/`)。

---

## 12. 参考

**AI 协作研究谱系(答辩素材,CLAUDE.md §9 / rule.md 30%):**
- `/root/AgentHub/RESEARCH_方法迭代_深研版.md` — v40,130+ 来源,六阶段漏斗 + 能力阶梯 + 三铁律
- `/root/AgentHub/ULTIMATE_多Agent协作防冲突_终极方案.md` — v23,"协作即版本化乐观事务"
- `/root/AgentHub/CODE_REVIEW_diff链路.md` — POC diff 链路 17 缺陷(C1–C5 避坑来源)
- 理论锚点:CALM/I-confluence(单调免协调)· SSI/write-skew · Bayou(可执行断言)· jj 一等冲突 + git rerere · mergiraf/weave(AST 合并)· Tricorder 10% 误报红线

**本设计产出过程:**
- `conflict-closed-loop-design` workflow — 8 路 ground-truth + 3 设计(Real-Sequential-Merge / CAP-E / CCT)+ 评分(winner 36/40)+ 对抗复审(git 2.25.1 实测验证)

**项目内:**
- ADR-003(workspace-shared git)· ADR-005(merge mode)· ADR-009(manual long-poll)· ADR-013/014/015 · ADR-016
- LIVE 代码:`api/routes.py:_merge_burst_to_main`(1683)· `sandbox/_core.py:merge_branch_into_main`(542)· `mcp/tools.py`(pending-edit gate + _callback_server)

---

*文档结束。待 review 后:① 落 ADR-017(冲突闭环:真合并探测 + 一等 conflict 数据)② 按 P1 → P1b → P2 实施,P1 上线前安全三件(锁 / 崩溃 guard / 冲突类型分类)必须先落。*
