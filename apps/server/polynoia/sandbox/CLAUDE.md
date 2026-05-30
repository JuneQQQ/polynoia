# sandbox/ — 工作区共享 git(冲突闭环承重区)

> 你在编辑 sandbox。`_core.py` 的 workspace git helper 被「冲突闭环」功能(`feature/diff_dev`)依赖。
> **改下面任何一个之前**,先读 [`/docs/design/conflict-closed-loop-CHARTER.md`](../../../../docs/design/conflict-closed-loop-CHARTER.md)。

🔴 **承重,改了会炸合并 / 冲突闭环**:
- `merge_branch_into_main`(:542)— 底层合并,**保签名**(冲突闭环用新 `probe_merge` 不复用它)
- `_workspace_run`(:574)— 所有 git 命令在 **workspace ROOT** 跑,返回 `(rc,out,err)`,别改执行上下文/返回格式
- `open_workspace_if_exists`(:190)— **sync `@classmethod`**,调用处**不能加 `await`**
- `commit_pending_worktrees`(:447)— 合并前收 worktree 未 commit 改动,别删
- `list_agent_branches`(:419)/ `branch_ahead_of_main`(:499)— 合并前置查询

⚠️ **不变量**:workspace root 单 HEAD,**绝不留半合并**(异常出口要守卫式 `git merge --abort`,先 `git rev-parse -q --verify MERGE_HEAD`);per-workspace 锁键是 `workspace_id` 不是 `conv_id`;binary 冲突**不能** UTF-8 decode。

🟢 自由动:`probe_merge` / `conclude_merge`(冲突闭环新增,功能私有)。
❌ 别碰:`git init -b main`(2.25.1 有 bug,用 `git symbolic-ref`);别复活 `orchestrator/runtime.py` 的死代码。
