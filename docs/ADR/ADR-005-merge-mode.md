# ADR-005 — Merge mode:auto vs manual,per-edit user gating

- **状态**:partial — auto 已实装,manual 设计已锁定但代码未上
- **日期**:2026-05-28
- **相关**:`docs/diagrams/merge-flow.html`,`apps/server/polynoia/orchestrator/runtime.py:_maybe_run_merge_phase`,4 个 merge_helpers 测试

## 背景

群聊 + workspace + 多 agent 并行 → 每 agent 在自己分支上干完,要不要 / 怎么合并到 main?Spec 没说。Cursor / Windsurf / 等 IDE 给的"per-edit 实时审批"模式 vs 全自动 Orchestrator merge,两种思路截然不同。

## 决策

**两种模式共存,用户在 Conversation 级别切换**:

### Auto 模式(默认)— 已实装
- 子任务完成 → Orchestrator AGGREGATE 后跑 Phase 6 MERGE
- 逐分支 `git merge --no-ff` 进 main
- 冲突 → `git merge --abort` + 标"待手动"卡
- 用户看到结果卡:`✓ branch_A → sha_abc · ⚠ branch_B 冲突`

### Manual 模式 — 设计已锁定,代码待上
- 每个 `edit_file` 工具调用悬挂(`await ctx.await_user_decision()`)
- 推 `PendingEdit` 卡到 UI,用户点 ✓ / ✗
- ✓:醒来,真写文件 + 提交到 agent 分支
- ✗:返回 `{"error": "rejected"}` 给 LLM,LLM 重新想方案
- sub-task 完成时分支末端 = "用户全程过审" → 直接 fast-forward merge 进 main

切换:`PATCH /api/conversations/{id}/merge_mode {mode: "auto"|"manual"}`,ChatPane header 一个 segmented toggle。新对话从 `Workspace.default_merge_mode` 继承。

## 为什么这两种都要

- **Auto**:并行实验场景(快速试方案),不想用户每步点确认
- **Manual**:严肃代码评审场景(写生产代码),用户要全程把关
- 同一对话切换可以,只影响**未来**的 edit/merge

## 决策细节

- LLM-驱动的冲突自动解决(Orchestrator 在冲突时读 `<<<<<` 标记自己解)推 P2 — 需要给 orchestrator 拿一份"merge phase 全量工具"的 session
- Manual 模式悬挂粒度:**只挂这一个 coroutine**,事件循环正常跑,其它 agent / 其它 conv 不影响
- 超时策略:Manual pending edit 超过 N 分钟无回应 → 自动 reject(可配)— 待实装

## 否则会怎样

- 只做 auto → 严肃场景用户不敢用
- 只做 manual → 试错场景烦死
- 单选一种都跟 Cursor / Windsurf / Claude Code IDE 模式发散

## 工作量分布

- Auto 一刀:DB 字段 + sandbox 工作区 git helper + Orchestrator Phase 6 → 已上线
- Manual 一刀:MCP `_EditTool` 改造 + PendingEdit 表 + WS approve/reject + DiffPart 真接 WS → 待 1-2 天工作量
