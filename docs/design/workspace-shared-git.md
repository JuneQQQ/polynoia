# Workspace-shared git + per-agent branches

> 2026-05-28。从 per-conv sandbox(实验室隔离)迁到 per-workspace git(团队协作)。
> 三段路线:P1.1 → P1.2 → P2,本文档落实 P1.1。

## 1. 目录布局

```
~/sandbox/polynoia/
├── <conv_id>/                          ← legacy 路径(DM 用,P1.1 不动)
│   ├── .git/
│   ├── .polynoia/
│   └── ...
└── workspaces/
    └── <workspace_id>/                 ← 新路径(group conv 用)
        ├── .git/                       ← workspace 共享 git
        ├── .gitignore                  ← ignore .polynoia/ 和 worktrees/
        ├── .polynoia/                  ← workspace 级凭证 + audit(共享)
        │   ├── credentials/
        │   │   ├── .claude/
        │   │   ├── .codex/
        │   │   └── ...
        │   ├── manifest.json
        │   └── audit.jsonl
        └── worktrees/                  ← 每个 (agent, conv) 一个 worktree
            ├── ag-{agent_id_short}-conv-{conv_id_short}/
            │   └── (working tree on branch agent/{agent_id}/conv-{conv_id})
            └── ...
```

**关键点**:
- `.git/` 单份(在 workspace root),所有 agent 通过 worktree 共享对象库
- `.polynoia/credentials/` 单份(workspace 级),所有 agent 的 HOME 指过来
- 每个 (agent, conv) 对**独立 worktree + 独立 branch**,互不踩脚

## 2. 分支命名

`agent/{agent_id}/conv-{conv_id}`

- agent_id 用 ULID 后 8 位 hex(防超长 + 唯一性够):`agent/01KSAB12/conv-01KSCD34`
- main 是项目共识分支,初始空 / 一个 README + .gitignore

## 3. P1.1 触发条件

`if conv.workspace_id is not None AND conv.group: 用 workspace sandbox`

DM 走 legacy per-conv 路径(P1.2 再迁)。

## 4. Sandbox API 改动

新增 classmethod `Sandbox.create_workspace_sandbox(*, workspace_id, conv_id, agent_id)`:

1. 检查 workspace dir 存在,不存在则 `_init_workspace`:
   - `mkdir -p <workspace>/`
   - `git init -b main`
   - 写 `.gitignore`(.polynoia/, worktrees/)
   - 拷凭证到 `.polynoia/credentials/`
   - 写 `.polynoia/manifest.json`
   - 初始 commit `polynoia: workspace init for ws <id>`
2. 检查目标 worktree 存在,不存在则:
   - `git worktree add -b agent/{X}/conv-{Y} worktrees/ag-{X_short}-conv-{Y_short}/`(从 main HEAD 分叉)
3. 返回 Sandbox 对象,`root` 指向 worktree 路径

## 5. env / HOME 重写

worktree 内的 agent 进程拿到:
- `HOME` → `<workspace>/.polynoia/credentials/`(共享凭证)
- `POLYNOIA_CONV_ID` → conv_id
- `POLYNOIA_AGENT_ID` → agent_id
- `POLYNOIA_WORKSPACE_ID` → workspace_id(新增)
- `POLYNOIA_SANDBOX_ROOT` → `~/sandbox/polynoia`(MCP 子进程用)

## 6. 合并策略

P1.1 **不自动合并**。Agent 在自己的分支上 commit,分支保留。

后续选项:
- **协作模式**(P1.2 默认):turn 完成后 `git merge --no-ff` 进 main,失败时分支保留 + UI 警告
- **谨慎模式**:始终保留分支,UI 等用户点 "review & merge"
- **Orchestrator 编排模式**(P2):复杂任务由 orchestrator 决定 merge 顺序

## 7. Audit & timeline 位置

- `audit.jsonl`(MCP 工具调用日志)→ workspace 级共享(`<workspace>/.polynoia/audit.jsonl`)
- `timeline.jsonl`(对话时间线)→ **保留 conv 级** 在 worktree 里;DB 已是上下文真相来源,timeline 是冗余但 sandbox 内 quick-grep 仍有用

## 8. 与上下文系统的协同

L3 ledger 的 git commit 部分(刚加的 `_pull_git_log_for_conv`)需要更新:
- 老路径:`Sandbox.open_if_exists(conv_id)` → 拉 `<conv>/.git`
- 新路径:`Sandbox.open_workspace_if_exists(workspace_id)` + `git log <branch>` 拿该 conv 分支的 commits

这意味着 **跨 conv 共享 workspace 时,A 在 main 上看到 B 的合并** — 真正的"workspace 共享代码变更"语义。

## 9. 测试覆盖

- workspace 首次创建 → 目录 + git + 初始 commit + 凭证拷贝就位
- 同 workspace 两个 (agent, conv) → 两个独立 worktree + 独立 branch + 共享 .git
- 同 (agent, conv) 多次调用 → 复用现有 worktree(idempotent)
- 跨 worktree commit:agent A 在 branch X 改文件并 commit,agent B 切到 branch Y `git log main` 看不到 A 的(未 merge),`git log agent/X/conv-Z` 能看到
- conv 删除 → `git worktree remove` 清理(P1.2 加)

## 10. 渐进 P1.2 / P2 工作量预估

- **P1.2**(DM 也归 workspace):需要 workspace_id 给所有 conv 兜底(默认 workspace 概念),或让 DM 在 workspace 内创建。改动 schema/UI
- **P2**(LLM 自动 merge 冲突 + UI):需要新 UI panel + LLM call 解决 hunk 冲突
