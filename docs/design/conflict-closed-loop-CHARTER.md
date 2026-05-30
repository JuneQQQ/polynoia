# 冲突闭环 · 开发边界宪章(Feature Charter)

> **动这块代码之前,先读这 2 分钟。** 这是 `feature/diff_dev` 上"冲突闭环"功能的开发入口。
> 完整设计:[`conflict-closed-loop-2026-05-30.md`](./conflict-closed-loop-2026-05-30.md)。
> **谁该读**:任何在 `api/routes.py` 合并/burst 区、`sandbox/_core.py` git helper、pending-edit 轨道、前端 store/PreviewPane/PARTS_REGISTRY 附近改代码的人(**即使你在做无关任务**)。
> 创建:2026-05-31 · owner:`feature/diff_dev`

---

## 0. 为什么有这份东西

冲突闭环复用了一批**全项目共享的承重机制**(burst 调度、workspace git 合并、pending-edit 轨道、消息卡注册表)。这些代码会因为各种**无关**原因被改(加端点、改 burst UI、重构 sandbox)。一次"顺手重构"很可能**静默**弄坏:① 已有的自动合并,② 正在建的冲突闭环。本宪章划清边界:**碰了会炸的 vs 可自由动的 vs 要协调的**。

---

## 1. 机制入口地图(LIVE 路径,改之前先认清)

```
用户消息
  → dispatch_user_message            routes.py:2239
  → run_adapter_turn                 routes.py:1704
  → drain _pending_dispatches        routes.py:2022
  → _spawn_turn(workers)             routes.py:2143
  → _mark_burst_task(is_last)        routes.py:1599 / is_last:1611   ← burst 状态机,触发合并
  → _merge_burst_to_main(reg)        routes.py:1683                  ← ★唯一真实合并点(冲突闭环挂这里)
       ├ commit_pending_worktrees    _core.py:447
       ├ list_agent_branches         _core.py:419
       ├ branch_ahead_of_main        _core.py:499
       └ merge_branch_into_main      _core.py:542 → _workspace_run:574(在 workspace ROOT 跑)
```

> ⚠️ `orchestrator/runtime.py` 的 `OrchestratorRuntime` / `_maybe_run_merge_phase` / `_detect_conflicts` 是**死代码**(只在测试 import,routes.py:2285 注释明说 deliberately bypass)。**别复活它、别往它上面加东西、别照抄它的 `git init -b main`(2.25.1 有 bug)。**

---

## 2. 爆炸半径:三类符号

### 🔴 共享/承重 —— 改了会炸(别人很可能碰到)

| 符号 | 位置 | 改了会怎样 |
|---|---|---|
| `_merge_burst_to_main` | routes.py:1683 | 唯一真实合并点;改签名/调用时机/返回处理 → 合并 + 冲突闭环全断 |
| `_mark_burst_task` / `is_last` | routes.py:1599 / 1611 | burst 状态机;`is_last` 必须同步判定 + 立即 pop,否则双触发或永不合并;也驱动 BurstCard UI |
| `_conv_bursts` 注册表 | routes.py:71 | 键结构 `{payload,pending,orch,workspace_id,contract}`;TasksCard/summary/merge 都读它 |
| `merge_branch_into_main` | _core.py:542 | 底层合并;**保留原样**(冲突闭环用新 `probe_merge` 不复用它,但别改它签名) |
| `_workspace_run` | _core.py:574 | 所有 git 命令在 **workspace ROOT** 跑(共享 `.git/`),返回 `(rc,out,err)`;改执行上下文/返回格式 → 全 git 操作错乱 |
| `open_workspace_if_exists` | _core.py:190 | **sync `@classmethod`**;调用处**不能加 `await`** |
| `commit_pending_worktrees` | _core.py:447 | 合并前扫 worktree 收未 commit 改动(OpenCode 原生写);删它 → 漏改动 |
| `_broadcast_to_conv` | routes.py:166 | 所有 `data-*` 卡的 WS 全广播;改它 → 多 tab/刷新同步断 |
| `Conversation.merge_mode` / `conv.workspace_id` | storage/models.py / routes.py:1694 | auto/manual 决策 + workspace 隔离根;语义变了 → 合并走错路径 |
| `PARTS_REGISTRY` | parts/index.tsx | 卡分派表;改名/复用 kind → 卡进黑洞 |
| pending-edit 轨道 | routes.py:1068/1106/1131/1161 · `_gate_via_pending_edit` tools.py:40 | 冲突闭环**镜像**它;重构它**必须同步改 conflict**(见 §4 协调) |
| `pendingEditsByConv` + preview 槽 | store.ts · PreviewPane | 与 conflict **共用 preview 右槽,互斥**;改互斥逻辑 → 两个 pane 打架 |

### 🟢 功能私有 —— 可自由动(冲突闭环建成后)

`ConflictRow` + repo / `/api/conflicts/*` 端点 / `probe_merge` · `conclude_merge`(_core.py 新增)/ `ConflictPart` · `ConflictResolvePane` / `conflictsByConv` store slice / `resolve_conflict` MCP 工具 / `_ws_merge_locks`。这些是新增、隔离的,改它们不影响别处。

### 🟡 无关但同文件 —— 随便改

`_pending_dispatches`(orchestrator 内部队列)、`_conv_outboxes`/`_conv_agent_tasks`/`_conv_agent_locks`/`_conv_inflight`(单 agent 生命周期)、mention 解析常量、其它无关 routes 端点、DiffTab 里的 @git-diff-view 用法。

---

## 3. 不变量(碰了会**静默**坏,测试不一定红)

1. **workspace root 单 HEAD,绝不留半合并。** 跨所有 worktree + 所有 conv 只有一个 HEAD/index;任何 merge 异常出口必须守卫式 `git merge --abort`(先 `git rev-parse -q --verify MERGE_HEAD` 判断,否则 rc=128)。
2. **per-workspace 锁键是 `workspace_id`,不是 `conv_id`。** 同 workspace 的多个 conv 并发合并会踩坏 `.git`。锁要包 `commit_pending_worktrees → probe_merge → conclude_merge` 整段。(此锁尚未存在,P1 必加。)
3. **`open_workspace_if_exists` 是 sync** —— 别加 await。
4. **新消息卡 kind 必须进 `MessagePayload` union + 跑 `make types`** —— 永不手写 `packages/shared` / `lib/types.ts`(CLAUDE.md §6.1)。
5. **pending-edit pane 与 conflict pane 共用 preview 右槽,互斥** —— 不能同时显。
6. **binary 冲突不能 UTF-8 decode**(`_workspace_run` 的 `.decode('utf-8','replace')` 会损坏)—— 走 take-side,blob 存 base64。
7. **append_message 存 raw dict 无 Pydantic 校验**(repo.py:515/525)—— 服务端不会因未知 kind 崩,但前端类型仍必须靠 union 生成。

---

## 4. 能做 / 不能做 / 要协调

**❌ 不能改(除非先读设计 doc §5 + 协调 owner):**
- `_merge_burst_to_main` 的签名 / 调用时机 / 返回处理
- `_conv_bursts` reg 的键结构
- `_mark_burst_task` 的 `is_last` 逻辑
- `merge_branch_into_main` / `_workspace_run` 的签名,或"在 workspace root 合并"这个不变量
- 给 `open_workspace_if_exists` 加 await
- 改名 / 复用 `PARTS_REGISTRY` 里的 kind
- 复活 `OrchestratorRuntime`

**✅ 可以做(有明确镜像路径,低风险):**
- 加 conflict 端点 / `ConflictRow` / `ConflictPart` / store slice / MCP 工具 —— 全部镜像 pending-edit
- 改无关的 routes 端点 / 无关的 part / 无关的 store slice

**⚠️ 要协调(跨功能但可控):**
- **重构 pending-edit 轨道** → 必须**同一 PR 内同步改 conflict**(它逐一镜像)
- **改 burst 机制** → 连带影响 BurstCard UI + mention-chain + LLM summary + 合并触发
- **动 `merge_mode` / `workspace_id` 语义** → 影响合并走哪条路径

---

## 5. 如果你必须动一个 🔴 共享符号(协议)

1. 先读 [`conflict-closed-loop-2026-05-30.md`](./conflict-closed-loop-2026-05-30.md),尤其 §5(关键正确性约束)。
2. **保签名、保调用时机、保返回处理**;只在内部做最小改动。
3. 如果你改的是被镜像的轨道(pending-edit),**同一 PR 里把 conflict 那侧也改了**。
4. 跑:`cd apps/server && uv run pytest -q` + `cd apps/web && ./node_modules/.bin/tsc -b`。
5. 在 commit/PR 里点名:`touched <symbol> — see conflict-closed-loop-CHARTER.md`,@ `feature/diff_dev` owner。

---

*本宪章是"活的所有权边界"(借深研版的 CODEOWNERS / 设计账本思路:对 agent,软纪律要硬化成边界 + 行动前注入)。功能合并进 main 后,把 🔴/🟢 清单更新为最终落地的符号,并考虑降级为常规 ADR。*
