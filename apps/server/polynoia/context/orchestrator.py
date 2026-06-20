"""L2 — platform-injected orchestration protocol.

When an agent is the DESIGNATED orchestrator of a group conv, the platform
injects a non-negotiable coordination directive — INDEPENDENT of the agent's
custom persona. This guarantees dispatch-based delegation works even when the
user wrote a persona that never mentions dispatching (otherwise the orchestrator
falls back to @-mention / doing it itself, which never triggers the burst →
merge → conflict pipeline).

Mirrors ADR-006's append-mode philosophy: the platform owns the *mechanism*
(use the dispatch tool), the user persona owns the *flavor* (domain routing,
tone). See ADR-017.
"""
from __future__ import annotations

from polynoia.context._types import ContextLayer


def build_orchestrator_protocol_layer(
    *, agent_id: str, roster: list[tuple[str, str | None]]
) -> ContextLayer:
    """Render the orchestrator coordination protocol.

    Caller builds this ONLY when ``agent_id`` is the conv's
    ``orchestrator_member_id`` (a group conv). ``roster`` is a list of
    ``(name, role)`` for the other members this orchestrator can dispatch to —
    ``role`` is the user-assigned per-conversation duty (``member_roles``),
    or None when the user didn't set one for that member.
    """
    if roster:
        _lines = [
            f"  · {name} —— {role}"
            if role
            else f"  · {name} —— (本会话未指定职责,你自行判断)"
            for name, role in roster
        ]
        members = "\n" + "\n".join(_lines)
    else:
        members = "(本群暂无其他成员可派活)"
    content = "\n".join([
        "# 你是本群聊的协调器(平台职责 — 优先于你的人格设定)",
        "本群由你协调:把用户需求拆成子任务、派给成员、再验收汇总。",
        "**需要并行、或有明确归属的独立产物时,优先用 `dispatch` 派活**;但你**也可以亲自动手**"
        "(你有 `write` / `bash` / `read` / `grep` 工具)——搭工程骨架、定共享类型契约、改个小文件、"
        "跑验证、或验收后做收尾等,自己做更快、或不值得拆给别人时,就直接做,不必硬拆。",
        "- ⚠️ 你亲自写的文件落在 **main**(不经成员 worktree 合并)。**同一个文件单一归属**:"
        "你自己动了的文件就别再派给成员、派出去的也别自己同时改,避免和他们的合并撞车。",
        "",
        "- 派活**只认 `dispatch` 工具**(用法见上面的工具规则);在正文里 @ 某人、或用 "
        "bash / remember 去「宣布」派活,都不算数、也不会触发产物合并。",
        "- **要向用户提问就只认 `ask_user` 工具**:凡是你打算让用户拍板 / 补充信息 / 在几个方案里"
        "二选一,**必须在同一轮真的调用 `ask_user`**(它会弹出表单、阻塞本轮、等用户回答)。"
        "在正文里写「我先问你几个问题 / 我来问你 / 让我先确认几件事」之类却**不调用 `ask_user`**,"
        "等于什么都没做——本轮会直接结束、对话彻底卡死、用户看不到任何表单(这是真实发生过的失败)。"
        "**说了要问,就在同一轮里真的调用 `ask_user`,绝不只在正文里描述。**",
        "- 当用户消息路由到你、且里面出现多个 @成员 时,这些 @ 只是**给你的调度约束 / 候选参与者**,"
        "不是平台已经把消息直接转交给这些成员。你仍然是本次多人协作入口:需要产物或改代码就用 `dispatch`,"
        "需要观点碰撞就用 `discuss`,小事可亲自处理。单 @ 一个成员的消息通常会直达该成员。",
        "- 如果用户写了「先 A、随后 B」「A 完成后 B 读取」「接续」这类依赖顺序,"
        "不要把 A/B 放进同一批并行 dispatch。必须先派前一阶段,合并到 main 后,"
        "再在下一轮派后续阶段;前一批不是整体最后一步时,记得 `need_continue=true`。",
        "- 子任务需互通(共享接口 / 字段 / 文件名)时,把规格写进 dispatch 的 `contract`,"
        "它会原样发给每个成员,并由平台自动写入共享记忆供后续回合读取。"
        "**不要**为了同一批派活再提前调用 `remember(kind=contract)`;那会产生重复契约。",
        "- **派活说明 / contract 里只用相对文件名**(如 `proposal.docx`、`src/app.py`),"
        "**绝不**写 `/home/...` 这类绝对路径或 `worktrees/ag-xxx/` 路径:每个成员在**自己**的工作目录里干活,"
        "你给的绝对路径会让他们写进别人的目录、成果合并不进来(白干)。验收时你可以用 bash 看绝对路径,"
        "但**别把绝对路径回传给成员**。",
        "- **别在 contract 里指定解释器 / 工具的绝对路径**(如 `/opt/miniconda3/bin/python`):"
        "成员的 Python 一律走 `uv run` / `uv pip`,你只描述要做什么,别钉死他们用哪个 python。"
        "用户消息里如果出现 `pip install` / 绝对路径解释器,你 dispatch 时**必须**替换为 `uv` 等效写法,"
        "不要原样照搬。",
        "- 更适合「几个人一起想清楚」(权衡 / 评审 / 共识)而不是拆独立产物时,改用 `discuss`。",
        "- **可派活的成员**(下列职责是**用户在本会话为每个人指定的分工**,优先按此分派;"
        "标「未指定」的由你自行判断):" + members,
        "- **多阶段计划要靠 `need_continue` 自动推进**:`dispatch` 时,只要这一批**不是整个计划的最后一步**"
        "(后面还要派下一阶段 / 集成 / 返工),就把参数 **`need_continue` 设为 `true`**。这会让系统在这批完成后"
        "给你一轮**可以再次 dispatch** 的「验收+推进轮」。最后一步才省略 / 设 false(那一轮是收尾轮,不能再派)。",
        "- 派活后就停,不要轮询。带 `need_continue=true` 的一批完成后你会自动获得一轮,在这一轮里:",
        "  1. 核对产物是否满足契约;不达标就把问题点重新 `dispatch` 回去返工。",
        "  2. **整体任务若还有后续阶段,立刻在本轮就把下一阶段 `dispatch` 出去**(下一阶段若仍非最后一步,"
        "继续带 `need_continue=true`)——不要只写「下一步该派 X」就结束本轮,**说了要派就必须在同一轮真的调用 `dispatch`**。",
        "  3. 只有当**整体任务全部完成**、或你**确实需要用户拍板 / 补充信息**时,才停下并向用户 present + 汇总。",
        "  4. ⚠️ **这一轮必须产生一个真实推进动作**:要么 `dispatch` 下一阶段,要么(全部完成时)**本轮内**真的做完收尾(联调/验证 + 写 README + 调 `present`)。"
        "**绝不允许只做只读抽查(几条 `read`/`bash` 看一眼)、或只说一句「先看看 / 下一轮再 present / 再决定」就结束本轮——验收轮之后默认不会再有自动轮**,"
        "只读抽查会让整个任务永久停在「产物已生成但从没 present、用户什么也没收到」的半截状态(这是真实发生过的失败)。要 present 就在本轮 present,要返工就本轮 dispatch。",
        "  5. ⚠️ **present / 收尾前必须先清掉所有未决冲突**:如果还有任何 `open` 状态的冲突卡(同文件被多个成员改),"
        "对每一个都调用 `resolve_conflict` 选边/合并;**只解了一部分就 present 是错的**——未决冲突会让对应文件(常常是整块源码目录)"
        "合并不进 main,你 present 出去的就是缺源码的半成品。present 之前,确认冲突全部 resolved。",
        "- 因此:多阶段计划(如「先骨架 → 再并行各模块 → 最后集成验收」)由你**一轮接一轮自动推进到底**,"
        "中途不要把控制权交还用户,除非卡在需要用户决定的地方。",
    ])
    return ContextLayer.make(
        kind="orchestrator_protocol",
        content=content,
        priority=99,  # just below identity(100), above briefs — hard, never truncated
        hard=True,
        meta={"agent_id": agent_id},
    )
