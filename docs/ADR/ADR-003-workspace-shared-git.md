# ADR-003 — Workspace 共享 git,每个 (agent, conv) 一条分支 + worktree

- **状态**:accepted
- **日期**:2026-05-27
- **相关**:`apps/server/polynoia/sandbox/_core.py`,`docs/design/workspace-shared-git.md`,7 个 workspace sandbox 测试

## 背景

P0 用 **per-conv sandbox**:每个对话独立 `~/sandbox/polynoia/<conv_id>/`,各自 `.git`。

问题暴露:
- 同 workspace 里多个 agent 各干各的,产出散在各 conv,**没法 merge**
- 想"先让 @claudeCode 加 discount,再让 @codex 加 currency,Orchestrator merge 二者" → 现有架构没法做
- 跨 conv 共享代码 base(workspace 是项目级)在 per-conv 模型里完全断了

## 决策

**双模式并存**:

### Legacy per-conv 模式(DM / 无 workspace 的对话)
- `~/sandbox/polynoia/<conv_id>/` 不变,独立 `.git` 独立 credentials

### Workspace-shared 模式(workspace 内对话)
- 一个 workspace = 一个共享 `.git`(`~/sandbox/polynoia/workspaces/<ws_id>/.git`)
- 每 (agent, conv) = 一条分支 `agent/{agent_id}/conv-{conv_id}` + 一个 worktree(`worktrees/ag-{X_short}-conv-{Y_short}/`)
- credentials 工作区级共享(单份 copy 复用)
- Pool 在 `get_session` 时根据 `conv.workspace_id` 决定走哪条

## 为什么

- **能 merge** — 多 agent 各在自己分支上干完,Orchestrator 跑 `git merge --no-ff` 收入 main(P1.2 auto mode 已实装)
- **隔离** — worktree 模式让 agent A/B 同时编辑互不踩坑,git 帮忙 dedupe object store
- **凭证不重复** — workspace 内多个 agent 共用一套 credentials copy,磁盘占用 ÷N
- **平滑升级** — legacy 模式继续工作,DM 类对话不需要这套重机制

## 否则会怎样

- merge mode auto/manual 都做不出来,rule.md "代码冲突处理" 这条直接失分
- 同 workspace 多 conv 没有共享代码 base,用户体感会断裂

## 代价

- Sandbox 类要双模(`is_workspace_mode` 分支)+ 一组工作区级 git helper(`merge_branch_into_main` 等)
- DB 加 `Workspace.default_merge_mode` + `Conversation.merge_mode` + `Conversation.workspace_id` 三字段联动

## 关键细节

- 合并不在 worktree 跑,在 workspace root 跑(`_workspace_run`)— worktree 各自 checkout 着分支,在 root 才能 `git checkout main && git merge ...`
- 冲突 → `git merge --abort`,main 保持干净,emit "待手动" 状态
- 自动合并的 LLM 介入(`merge-flow.html` 路径 B 第 6 步)推 P2
