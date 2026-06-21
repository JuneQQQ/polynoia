# ADR-024 — rewind/restore 的「在跑」守卫扩到 workspace 级(而非会话级)

**日期**:2026-06-22
**状态**:Accepted
**相关**:[ADR-003](ADR-003-workspace-shared-git.md)(workspace 共享 git + per-agent worktree)、[ADR-014](ADR-014-handoff-contract-and-shared-memory.md)(conv 级共享记忆,rewind 会一并裁剪)、[ADR-005](ADR-005-merge-mode.md)(合并模式 / `workspace_merge_lock`)

## 背景 / 问题

两个面向用户的「时间旅行」操作会硬重置 **整个 workspace 共享的 `main`**:

- `从此处重来`(rewind,`POST /api/conversations/{id}/rewind`)— 删除某消息及其之后的对话;若该消息带 `code_sha` 检查点,还会把 workspace `main` 回退到那个 sha。
- `回到这个对话`(restore,`POST /api/workspaces/{ws}/restore`)— 直接把 `main` 硬重置到某 sha。

两者落到 git 时都走 `Sandbox.restore_main_to`(`sandbox/_core.py`):`git merge --abort`(打断进行中的合并)→ `git update-ref refs/polynoia/undo/<ts>`(留安全网)→ `git reset --hard <sha>`;并且 `close_all()` 驱逐**所有**会话的 adapter session。

**问题:破坏范围是 workspace 级,前置守卫却是会话级。** 两个端点的「有 agent 在跑吗」检查都只调 `_conv_has_running_agent(conv_id)`(`api/routes.py`),只看**被回滚的那一个会话**。而一个 workspace 可被多个会话共享(`PATCH /api/conversations/{id}/workspace` / 建会话时「接入现有工作区」只校验存在、不校验独占)。于是:

- 会话 A 空闲、会话 B 正在同一 workspace 上跑 agent / 正在合并;
- 用户在 A 点「从此处重来」→ 守卫看 A 是空的 → 放行;
- `close_all()` 掐断 B 的 turn、`git merge --abort` 打断 B 的合并、`git reset --hard` 把 B 已提交进 `main` 的成果从 HEAD 抹掉 —— **B 全程没被检查、没被询问、文件树也只通知发起方 A 不通知 B**。

**关键澄清(这不是数据竞争)**:`restore_main_to` 内部持 `workspace_merge_lock`(`sandbox/_core.py`),与真实合并轨道(`ws_conv.py` 的 drain)用同一把锁,所以 git 在字节层面始终自洽,**不会写坏**。问题纯粹是**守卫范围错配**:一个 workspace 级的破坏性动作,用了会话级的前置条件来把关。锁防住了「写坏」,没防住「不该在 B 还活着的时候,合法但灾难性地动共享 main」。

复现(前端可点出):A、B 挂同一 workspace → 在 B 让 agent 建并提交 `from_b.txt` → 在 A 回滚到「B 提交之前、且带 `code_sha` 检查点」的消息 → `from_b.txt` 从产物面板消失(B 的提交被 reset 掉)。注意锚点必须带检查点:workspace 还没首次 commit 时发的消息 `code_sha=None`,rewind 退化成纯聊天回滚、不碰 git。

## 决策

**把「在跑」判断从会话级扩到 workspace 级。** 新增:

```python
async def _workspace_has_running_agent(workspace_id, *, exclude_conv=None) -> bool:
    # 枚举所有 workspace_id 相同的会话,任一 _conv_has_running_agent 为真则真
```

两处使用:

1. `rewind_conversation`:**仅在破坏性代码回退路径**(`workspace_id and target_code_sha`)上,若同 workspace 任一兄弟会话在跑 → `409`。纯聊天回滚(无检查点)不碰 `main`,不受影响。
2. `restore_workspace`:动 `main` 前,若同 workspace 任一会话在跑 → `409`(restore 总是重置 main,故无条件检查)。

行为与既有的会话级 `409`(`an agent is still running — finish or cancel it first`)一致:用户需先取消/等完兄弟会话,再回滚。

## 不选的方案

- **把守卫 + reset 一起包进 `workspace_merge_lock` 来消灭 TOCTOU**:`restore_main_to` **内部已持这把锁**,`asyncio.Lock` 不可重入,外面再包会**死锁**。务实做法是守卫留在端点层(best-effort);残留的极小 TOCTOU 窗口无害,因为锁仍保证 git 不被写坏。彻底消除需把 `restore_main_to` 拆出「假定已持锁」的内层版本——成本不值,留待需要时。
- **Tier 2:不拒绝,改为重置后给所有共享会话广播刷新 + system 提示**:提升可见性,但不阻止「在 B 活着时掀基线」本身。作为后续增强,不替代本守卫。
- **Tier 3:把「聊天回滚」与「共享代码回退」解耦**(per-conv rewind 默认只回滚聊天 + 本会话分支;代码回退另走显式的、跨会话感知的 workspace 操作)——这是根因解法,但属较大设计改动,单独立项。本 ADR 先用最小守卫堵住数据丢失。

## 代价 / 风险

- **共享 workspace 下回滚更难**:只要任一兄弟会话在跑就被拒。这是对「破坏性重置共享 main」的正确取舍——宁可让用户先收尾,也不静默掀别人的活。
- **不解决跨会话同意/可见性**:B 的基线仍可能被 A 的回滚改变(只要 B 当时空闲),且只通知 A。需 Tier 2 跟进。
- **不解决语义耦合**:rewind 仍有权重置共享 main。根因需 Tier 3。
- **聊天侧硬删仍不可逆**:`delete_messages_from` 真删、无 tombstone;git 侧有 undo ref 可恢复,聊天侧不能。本 ADR 不触及。

## 验证

- 新增 `tests/api/test_rewind_workspace_guard.py`(3 例):兄弟会话在跑 → rewind A 返回 `409` 且**在任何删除前**就拒绝(A 的消息仍在);兄弟会话都空闲 → 越过守卫(随后因 workspace 未物化而 `404`,证明放行);纯聊天回滚(无检查点)→ 不被在跑的兄弟会话阻塞。
- `pytest tests/api/test_rewind_workspace_guard.py tests/api/test_rewind.py tests/api/test_rewind_replay.py` → 11 passed(含既有 rewind 回归)。
- 改动分支 `fix/rewind-workspace-scoped-guard`(commit `4e8ddb6`),仅动 `api/routes.py` 三处 + 新增测试;ruff 既有报错与本改动无关。
