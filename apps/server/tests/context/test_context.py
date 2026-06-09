"""Integration tests for the L1-L5 context assembler.

Validates the visibility model:
  · L1 identity always present
  · L2 briefs include workspaces this agent belongs to, exclude others
  · L3 ledger surfaces messages from convs this agent is a member of OR
    in workspaces this agent belongs to (sibling-conv code visibility);
    explicitly skips the current conv (L4 covers it)
  · L4 history pulls the rolling window of current conv only
  · Two contacts on the same adapter (1A: independent personas) get
    independent ledgers, not merged
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from polynoia.context import build_context_for_turn
from polynoia.domain.entities import (
    Agent,
    AgentSetup,
    AgentSkill,
    Conversation,
    Workspace,
    new_ulid,
)
import polynoia.storage.db as db_module
from polynoia.storage.models import MessageRow
from polynoia.storage.repo import (
    append_message,
    create_conversation,
    upsert_agent,
    upsert_workspace,
)


@pytest.fixture
async def clean_db(monkeypatch, tmp_path: Path):
    """Spin up a per-test SQLite, no leakage between tests.

    `polynoia.storage.db` builds `engine` + `SessionLocal` at module load,
    bound to whatever `settings.db_url` was then. We rebuild both with
    the test-temporary URL so each test gets its own clean DB.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db_url = f"sqlite+aiosqlite:///{tmp_path}/ctx-test.db"
    monkeypatch.setattr("polynoia.settings.settings.db_url", db_url)

    import polynoia.storage.db as db_mod
    new_engine = create_async_engine(
        db_url, echo=False, future=True,
        connect_args={"check_same_thread": False},
    )
    new_sm = async_sessionmaker(new_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", new_engine)
    monkeypatch.setattr(db_mod, "SessionLocal", new_sm)

    # Build schema in the new DB
    from polynoia.storage import models  # noqa: F401
    async with new_engine.begin() as conn:
        await conn.run_sync(db_mod.Base.metadata.create_all)
    try:
        yield
    finally:
        await new_engine.dispose()


async def _seed_agent(name: str, *, model: str = "claude-sonnet-4-6") -> Agent:
    a = Agent(
        id=new_ulid(),
        name=name,
        role="test",
        provider="claude",
        handle=f"@{name}",
        initials=name[:2],
        color="#000",
        bg="#fff",
        system_prompt=f"你是 {name},一个专注 {name} 的 agent。",
        setup=AgentSetup(adapter_id="claudeCode", model=model),
    )
    async with db_module.SessionLocal() as session:
        await upsert_agent(session, a)
        await session.commit()
    return a


async def _seed_workspace(name: str, members: list[str]) -> Workspace:
    w = Workspace(
        id=new_ulid(),
        server_id="local",
        name=name,
        members=members,
    )
    async with db_module.SessionLocal() as session:
        await upsert_workspace(session, w)
        await session.commit()
    return w


async def _seed_conv(
    title: str,
    members: list[str],
    *,
    workspace_id: str | None = None,
    direct: bool = False,
) -> Conversation:
    c = Conversation(
        id=new_ulid(),
        workspace_id=workspace_id,
        title=title,
        members=members,
        direct=direct,
        group=not direct,
    )
    async with db_module.SessionLocal() as session:
        await create_conversation(session, c)
        await session.commit()
    return c


async def _post_message(
    conv_id: str, sender_id: str, text: str, *, minutes_ago: int = 0
) -> None:
    ts = datetime.utcnow() - timedelta(minutes=minutes_ago)
    payload = {"kind": "text", "body": [{"t": "p", "c": text}]}
    async with db_module.SessionLocal() as session:
        session.add(
            MessageRow(
                id=new_ulid(),
                conv_id=conv_id,
                sender_id=sender_id,
                payload=payload,
                created_at=ts,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_identity_layer_always_present(clean_db) -> None:
    """L1 identity always at top, even with no other layers."""
    a = await _seed_agent("孤独 Agent")
    conv = await _seed_conv("一个空对话", members=["you", a.id], direct=True)
    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=a.id, conv_id=conv.id, user_text="你好"
        )
    assert "# 身份" in prompt
    assert "孤独 Agent" in prompt
    assert "你是 孤独 Agent" in prompt  # persona injected via system_prompt
    assert "# 当前用户消息" in prompt
    assert "你好" in prompt


@pytest.mark.asyncio
async def test_bound_skill_name_only_is_injected_from_skill_package(clean_db) -> None:
    """Contact editor stores bound skills by name; identity must still expose
    the package so the agent can answer what skills it has."""
    a = await _seed_agent("Deck Agent")
    a.skills = [AgentSkill(name="ppt-master", instructions="")]
    async with db_module.SessionLocal() as session:
        await upsert_agent(session, a)
        await session.commit()
    conv = await _seed_conv("技能确认", members=["you", a.id], direct=True)

    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=a.id, conv_id=conv.id, user_text="你有什么 skill?"
        )

    assert "## 你已装配的技能" in prompt
    assert "### ppt-master" in prompt
    assert "PPT Master" in prompt


@pytest.mark.asyncio
async def test_workspace_briefs_filter_by_membership(clean_db) -> None:
    """Agent only sees workspaces they're a member of. Briefs are project-scoped
    (R1): they render inside a PROJECT conv and are suppressed entirely in an
    out-of-project DM — so we assert membership filtering from a project conv."""
    alice = await _seed_agent("Alice")
    bob = await _seed_agent("Bob")
    aw = await _seed_workspace("Alice 的项目", members=["you", alice.id])
    await _seed_workspace("Bob 的私人项目", members=["you", bob.id])

    conv = await _seed_conv(
        "Alice 项目对话", members=["you", alice.id], workspace_id=aw.id,
    )
    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=conv.id, user_text="hi"
        )
    assert "Alice 的项目" in prompt
    assert "Bob 的私人项目" not in prompt


@pytest.mark.asyncio
async def test_group_membership_layer_surfaces_roster_and_recent_changes(clean_db) -> None:
    """Join/leave events are operational context, not best-effort chat history."""
    orch = await _seed_agent("阿核")
    writer = await _seed_agent("文澜")
    analyst = await _seed_agent("数擎")
    removed = await _seed_agent("旧成员")
    ws = await _seed_workspace(
        "PRD 项目", members=["you", orch.id, writer.id, analyst.id]
    )
    conv = Conversation(
        id=new_ulid(),
        workspace_id=ws.id,
        title="PRD 群聊",
        members=["you", orch.id, writer.id, analyst.id],
        group=True,
        orchestrator_member_id=orch.id,
        member_roles={
            writer.id: "章节撰写",
            analyst.id: "数据分析",
        },
    )
    async with db_module.SessionLocal() as session:
        await create_conversation(session, conv)
        await append_message(
            session,
            conv_id=conv.id,
            sender_id="system",
            payload={
                "kind": "text",
                "body": [
                    {
                        "t": "p",
                        "c": f"👥 成员变更 — 加入 @{analyst.name} · 移出 @{removed.name}",
                    }
                ],
            },
        )
        await session.commit()

    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=writer.id, conv_id=conv.id, user_text="继续"
        )

    assert "# 群成员与成员变更" in prompt
    assert "@文澜 (你) —— 章节撰写" in prompt
    assert "@阿核 (协调者)" in prompt
    assert "@数擎 —— 数据分析" in prompt
    assert "最近成员变更" in prompt
    assert "加入 @数擎" in prompt
    assert "移出 @旧成员" in prompt
    assert "只能 @ / dispatch 当前群成员" in prompt


@pytest.mark.asyncio
async def test_activity_ledger_respects_conv_membership(clean_db) -> None:
    """Agent A doesn't see content of conv where only Agent B was a member."""
    alice = await _seed_agent("Alice")
    bob = await _seed_agent("Bob")

    # Bob has a private DM the user shouldn't show to Alice
    bob_dm = await _seed_conv("Bob DM", members=["you", bob.id], direct=True)
    await _post_message(bob_dm.id, "you", "私下问 Bob 一件秘密事", minutes_ago=10)
    await _post_message(bob_dm.id, bob.id, "好的,我帮你看看", minutes_ago=9)

    # Alice has her own DM (current conv)
    alice_dm = await _seed_conv("Alice DM", members=["you", alice.id], direct=True)
    await _post_message(alice_dm.id, "you", "Alice 你好", minutes_ago=5)

    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=alice_dm.id, user_text="再说一次"
        )

    # Alice's L3 should NOT contain Bob's private DM
    assert "私下问 Bob" not in prompt
    assert "Bob DM" not in prompt  # conv title also private


@pytest.mark.asyncio
async def test_workspace_sibling_conv_visible(clean_db) -> None:
    """A is in workspace W. Even if A isn't in sibling conv X in W,
    X's content is visible (because W-shared code/activity)."""
    alice = await _seed_agent("Alice")
    bob = await _seed_agent("Bob")
    w = await _seed_workspace("共享项目", members=["you", alice.id, bob.id])

    # Bob is in a sibling conv inside W (Alice not in this conv's members)
    sibling = await _seed_conv(
        "Bob 单独搞事的子对话", members=["you", bob.id], workspace_id=w.id, direct=True,
    )
    await _post_message(sibling.id, bob.id, "我刚改了 server/auth.py", minutes_ago=15)

    # Alice's current conv is the workspace main
    main = await _seed_conv(
        "主对话", members=["you", alice.id, bob.id], workspace_id=w.id,
    )
    await _post_message(main.id, "you", "Alice 进来看看", minutes_ago=2)

    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=main.id, user_text="状态如何"
        )

    # Bob's sibling-conv work IS visible to Alice because they share workspace
    assert "改了 server/auth.py" in prompt


@pytest.mark.asyncio
async def test_two_contacts_on_same_adapter_have_independent_ledgers(
    clean_db,
) -> None:
    """1A decision: contacts on the same adapter are *different* personas.
    Claude-Fast and Claude-Hardcore each only see their own participated convs.
    """
    fast = await _seed_agent("Claude-Fast")
    hardcore = await _seed_agent("Claude-Hardcore")

    fast_dm = await _seed_conv("Fast DM", members=["you", fast.id], direct=True)
    await _post_message(fast_dm.id, "you", "快快快", minutes_ago=10)
    await _post_message(fast_dm.id, fast.id, "回复:快好了", minutes_ago=9)

    hardcore_dm = await _seed_conv(
        "Hardcore DM", members=["you", hardcore.id], direct=True
    )
    await _post_message(hardcore_dm.id, "you", "认真分析", minutes_ago=8)
    await _post_message(
        hardcore_dm.id, hardcore.id, "回复:经过深入推理…", minutes_ago=7
    )

    # Build prompt for Fast in a DIFFERENT (new) conv
    new_conv = await _seed_conv("New DM", members=["you", fast.id], direct=True)
    async with db_module.SessionLocal() as db:
        fast_prompt = await build_context_for_turn(
            db, agent_id=fast.id, conv_id=new_conv.id, user_text="嗨"
        )
        hard_prompt = await build_context_for_turn(
            db, agent_id=hardcore.id, conv_id=new_conv.id, user_text="嗨"
        )

    # Each sees only their own DM in the ledger
    assert "快好了" in fast_prompt
    assert "认真分析" not in fast_prompt
    assert "经过深入推理" not in fast_prompt

    assert "经过深入推理" in hard_prompt
    assert "快好了" not in hard_prompt
    assert "快快快" not in hard_prompt


@pytest.mark.asyncio
async def test_l4_history_pulls_rolling_window(clean_db) -> None:
    """L4 takes the last N messages from the current conv (window=100)."""
    alice = await _seed_agent("Alice")
    conv = await _seed_conv("Alice DM", members=["you", alice.id], direct=True)
    # Seed past the window so truncation is actually exercised.
    for i in range(110):
        await _post_message(
            conv.id, "you", f"消息 #{i}", minutes_ago=110 - i
        )

    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=conv.id, user_text="总结"
        )

    # Last 100 should appear (window default)
    assert "消息 #109" in prompt
    assert "消息 #15" in prompt
    # Messages older than the 100-window should NOT appear (#0–#9 dropped)
    assert "消息 #0" not in prompt


@pytest.mark.asyncio
async def test_ledger_surfaces_git_commits(clean_db, monkeypatch, tmp_path) -> None:
    """L3 ledger should include git commits from visible convs' sandboxes.

    Validates the user's original requirement: "agent perceives code changes
    by other agents in shared workspaces". Each conv has its own sandbox
    with an isolated git repo; the ledger walks visible convs and pulls
    `git log` for each.
    """
    import subprocess

    alice = await _seed_agent("Alice")
    bob = await _seed_agent("Bob")
    w = await _seed_workspace("共享项目", members=["you", alice.id, bob.id])

    # Bob's conv in the workspace (Alice is workspace-member but NOT conv-member)
    bob_conv = await _seed_conv(
        "Bob 单干", members=["you", bob.id], workspace_id=w.id, direct=True
    )
    await _post_message(bob_conv.id, bob.id, "我准备改文件", minutes_ago=20)

    # Manually seed Bob's sandbox + a git commit (simulating Bob's MCP-tool edits)
    sandbox_root = tmp_path / "sandboxes"
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", sandbox_root)
    bob_sandbox = sandbox_root / bob_conv.id
    bob_sandbox.mkdir(parents=True, exist_ok=True)
    # git 2.25.1 (Ubuntu 20.04) has no `git init -b`; set default branch portably.
    subprocess.run(["git", "init", "-q"], cwd=bob_sandbox, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=bob_sandbox, check=True
    )
    subprocess.run(["git", "config", "user.email", "test@x"], cwd=bob_sandbox, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=bob_sandbox, check=True)
    (bob_sandbox / "auth.py").write_text("# stub\n")
    subprocess.run(["git", "add", "auth.py"], cwd=bob_sandbox, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "Bob: add JWT auth middleware"],
        cwd=bob_sandbox, check=True,
    )

    # Alice's conv (current). She is in the workspace too → has visibility into
    # bob_conv via the workspace.
    alice_conv = await _seed_conv(
        "Alice 主对话", members=["you", alice.id, bob.id], workspace_id=w.id,
    )
    await _post_message(alice_conv.id, "you", "进度如何?", minutes_ago=1)

    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=alice_conv.id,
            user_text="bob 改了啥?",
        )

    # The commit subject from Bob's sandbox should appear in Alice's ledger
    assert "Bob: add JWT auth middleware" in prompt
    assert "📝" in prompt  # the 代码变更 section marker


@pytest.mark.asyncio
async def test_l5_user_turn_not_truncated_even_when_huge(clean_db) -> None:
    """Regression for review #1: L5 is hard — a 50k-token user paste must
    appear intact in the prompt even if it blows the user_turn cap.
    Without hard-layer protection, the user's actual question would be
    chopped, leading to wrong answers."""
    alice = await _seed_agent("Alice")
    conv = await _seed_conv("paste-test", members=["you", alice.id], direct=True)

    # Fake "user pasted a huge code block" — 30k chars of pseudo-code
    big_paste = "def function_x():\n    return 42\n" * 1500  # ~45k chars
    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=conv.id, user_text=big_paste,
        )

    # Full user paste must survive intact — search for a tail-of-paste marker
    assert "def function_x():\n    return 42\n" * 5 in prompt
    # Identity header must also survive (hard layer too)
    assert "# 身份" in prompt


@pytest.mark.asyncio
async def test_cjk_token_estimator_not_underestimating(clean_db) -> None:
    """Regression for review #2: estimate_tokens must NOT badly underestimate
    Chinese. Old `chars // 3` returned token count half of reality."""
    from polynoia.context.window import estimate_tokens
    chinese = "你好" * 100  # 200 CJK chars
    latin = "hello " * 100  # 600 latin chars
    # Old buggy estimator: chinese=66 (way too low), latin=200
    # New CJK-aware: chinese ≥ 200 (1.5/char), latin ~200
    assert estimate_tokens(chinese) >= 200
    assert estimate_tokens(chinese) > 200 // 3 + 50  # strictly better than chars//3


@pytest.mark.asyncio
async def test_dm_section_grouped_separately(clean_db) -> None:
    """Regression for review #9: DMs render under '## 私聊', workspace
    convs render under '## 项目 · <ws name>' — no mixing, no empty ws_label."""
    alice = await _seed_agent("Alice")
    bob = await _seed_agent("Bob")
    ws = await _seed_workspace("alpha 项目", members=["you", alice.id, bob.id])

    ws_conv = await _seed_conv(
        "项目主对话", members=["you", alice.id], workspace_id=ws.id,
    )
    await _post_message(ws_conv.id, "you", "项目里聊", minutes_ago=10)

    dm = await _seed_conv("私聊 DM", members=["you", alice.id], direct=True)
    await _post_message(dm.id, "you", "私聊内容", minutes_ago=5)

    # Current conv is IN the project — the project section is only rendered
    # in-project (R1); out-of-project DMs show only the 私聊 bucket.
    current = await _seed_conv(
        "当前", members=["you", alice.id], workspace_id=ws.id,
    )
    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=current.id, user_text="?"
        )
    assert "## 项目 · alpha 项目" in prompt
    assert "## 私聊" in prompt
    # Make sure DM title doesn't show under workspace section by accident
    ws_idx = prompt.index("## 项目 · alpha 项目")
    dm_idx = prompt.index("## 私聊")
    assert ws_idx < dm_idx, "workspace section should come before DM section"


@pytest.mark.asyncio
async def test_diff_payload_renders_with_file_info(clean_db) -> None:
    """Regression for review #13: non-text payloads (diff) get type-aware
    placeholders so the agent knows code changes happened."""
    alice = await _seed_agent("Alice")
    conv = await _seed_conv("Diff test", members=["you", alice.id], direct=True)
    diff_payload = {
        "kind": "diff",
        "files": [
            {"path": "src/app.ts", "additions": 12, "deletions": 3},
            {"path": "src/lib/util.ts", "additions": 5, "deletions": 0},
        ],
    }
    async with db_module.SessionLocal() as session:
        session.add(
            MessageRow(
                id=new_ulid(),
                conv_id=conv.id,
                sender_id=alice.id,
                payload=diff_payload,
                created_at=datetime.utcnow() - timedelta(minutes=5),
            )
        )
        await session.commit()

    other_conv = await _seed_conv("Other", members=["you", alice.id], direct=True)
    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=other_conv.id, user_text="?"
        )
    assert "src/app.ts" in prompt
    assert "(+12 -3)" in prompt


@pytest.mark.asyncio
async def test_tool_call_payload_with_dict_output_does_not_crash(clean_db) -> None:
    """Regression: a persisted tool-call row whose `output` is a structured
    dict (e.g. {"kind":"wrote",...}, no `summary`) must not crash history
    formatting. `_format_message_body` previously did `dict[:120]` → KeyError,
    which silently killed the orchestrator's summary turn (its context build
    includes the workers' persisted tool-call rows)."""
    alice = await _seed_agent("Alice")
    conv = await _seed_conv("Tool test", members=["you", alice.id], direct=True)
    tool_payload = {
        "kind": "tool-call",
        "tool_call_id": "tc1",
        "name": "write",
        "state": "completed",
        # No `summary` → falls back to `output`, which is a dict here.
        "output": {"kind": "wrote", "path": "api.py", "created": True, "bytes": 51},
    }
    async with db_module.SessionLocal() as session:
        session.add(
            MessageRow(
                id=new_ulid(),
                conv_id=conv.id,
                sender_id=alice.id,
                payload=tool_payload,
                created_at=datetime.utcnow() - timedelta(minutes=5),
            )
        )
        await session.commit()

    other_conv = await _seed_conv("Other2", members=["you", alice.id], direct=True)
    async with db_module.SessionLocal() as db:
        # Must not raise (was KeyError before the str-coerce fix).
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=other_conv.id, user_text="?"
        )
    assert "工具调用 write/completed" in prompt


@pytest.mark.asyncio
async def test_huge_single_message_gets_folded(clean_db) -> None:
    """A single mega-message is folded by per-message cap (head+tail kept),
    NOT by layer-budget truncation. Per-message cap fires first, so the
    layer never overflows. This is the right behavior — without it a single
    50k-token paste would consume the entire layer."""
    alice = await _seed_agent("Alice")
    conv = await _seed_conv("history-overflow", members=["you", alice.id], direct=True)

    await _post_message(conv.id, "you", "context starts here", minutes_ago=30)
    big = "x" * 150_000  # ~150k chars (mostly latin so ~50k tokens)
    await _post_message(conv.id, alice.id, big, minutes_ago=20)
    for i in range(5):
        await _post_message(conv.id, "you", f"tail msg {i}", minutes_ago=10 - i)

    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=conv.id, user_text="总结"
        )

    # Per-message fold marker should appear (NOT the layer-budget marker)
    assert "长内容已折叠" in prompt
    # The most recent tail messages must survive
    assert "tail msg 4" in prompt
    # Anchor message at the start should still be visible
    assert "context starts here" in prompt


@pytest.mark.asyncio
async def test_shared_memory_layer_injected(clean_db) -> None:
    """ADR-014: conv-scoped shared memory (contract/decision/artifact) is
    injected into a PROJECT (workspace) conversation's turn, kind-layered
    (contract before artifact)."""
    from polynoia.storage.repo import add_conv_memory

    alice = await _seed_agent("Alice")
    ws = await _seed_workspace("Proj", [alice.id])
    conv = await _seed_conv(
        "Shared mem", members=["you", alice.id], workspace_id=ws.id,
    )
    async with db_module.SessionLocal() as session:
        await add_conv_memory(
            session, conv_id=conv.id, author_agent_id="you",
            kind="contract", content="字段 id/title/done;GET|POST /todos;端口 8000",
        )
        await add_conv_memory(
            session, conv_id=conv.id, author_agent_id=alice.id,
            kind="artifact", content="Alice → api.py",
        )
        await session.commit()

    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=conv.id, user_text="继续",
        )
    assert "<shared_memory>" in prompt
    assert "[契约]" in prompt and "端口 8000" in prompt
    assert "[产物] Alice → api.py" in prompt


@pytest.mark.asyncio
async def test_external_dm_injects_agent_level_memory(clean_db) -> None:
    """ADR-019 + R1: in a project-EXTERNAL DM, the shared-memory layer switches
    to agent-level — the agent's OWN work across conversations. Teammates'
    project work is NOT proactively injected (R1: no project-detail leak into an
    out-of-project chat); it stays inside the project conv."""
    from polynoia.storage.repo import add_conv_memory

    alice = await _seed_agent("Alice")
    bob = await _seed_agent("Bob")
    ws = await _seed_workspace("Proj", [alice.id, bob.id])
    proj = await _seed_conv(
        "v1 group", members=["you", alice.id, bob.id], workspace_id=ws.id,
    )
    async with db_module.SessionLocal() as session:
        await add_conv_memory(
            session, conv_id=proj.id, author_agent_id=alice.id,
            kind="artifact", content="Alice 实现了 settle.py 的分账算法",
        )
        await add_conv_memory(
            session, conv_id=proj.id, author_agent_id=bob.id,
            kind="artifact", content="Bob 写了结算 UI",
        )
        await session.commit()

    # A standalone 1:1 DM with Alice — workspace_id=None → project-external.
    dm = await _seed_conv("问 Alice", members=["you", alice.id], direct=True)
    async with db_module.SessionLocal() as db:
        prompt = await build_context_for_turn(
            db, agent_id=alice.id, conv_id=dm.id, user_text="你最近做了啥?",
        )
    assert "<shared_memory>" in prompt
    # Alice's own cross-conv work surfaces under 我的工作 (answer-when-asked)
    assert "我的工作" in prompt and "settle.py 的分账算法" in prompt
    # R1: teammates' project work is NOT proactively injected out-of-project.
    assert "队友相关工作" not in prompt
    assert "Bob 写了结算 UI" not in prompt
