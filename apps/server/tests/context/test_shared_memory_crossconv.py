"""Cross-conversation agent memory in the shared-memory layer.

The product promise: 单聊里告诉过 agent 的事,群里它也该记得。Two pieces make
that true and both broke silently before (found by a live cross-chat probe):

  1. `remember` must attribute rows to the CONTACT's ULID (turn_agent_id), not
     the adapter's static id — covered by inspection + the layer tests below.
  2. The GROUP branch of build_shared_memory_layer must include the agent's OWN
     memory from OTHER conversations (it previously loaded only the conv board).
"""
from __future__ import annotations

import pytest

from polynoia.context.shared import build_shared_memory_layer
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


AGENT_A = "01AGENTAXXXXXXXXXXXXXXXXXX"
AGENT_B = "01AGENTBXXXXXXXXXXXXXXXXXX"


async def _setup(db) -> tuple[str, str]:
    """A DM (agent A) holding a remembered fact + a group with A and B."""
    dm = Conversation(
        id=new_ulid(), workspace_id=None, title="DM",
        members=["you", AGENT_A], direct=True, group=False,
    )
    grp = Conversation(
        id=new_ulid(), workspace_id=None, title="群",
        members=["you", AGENT_A, AGENT_B], direct=False, group=True,
        orchestrator_member_id=AGENT_A,
    )
    await storage_repo.create_conversation(db, dm)
    await storage_repo.create_conversation(db, grp)
    await storage_repo.add_conv_memory(
        db, conv_id=dm.id, author_agent_id=AGENT_A,
        kind="decision", content="发布代号: BLUE-FALCON-42",
    )
    await db.commit()
    return dm.id, grp.id


@pytest.mark.asyncio
async def test_group_layer_includes_agents_own_crossconv_memory(fresh_db) -> None:
    async with SessionLocal() as db:
        dm_id, grp_id = await _setup(db)
        layer = await build_shared_memory_layer(db, grp_id, agent_id=AGENT_A)
    assert layer is not None
    assert "BLUE-FALCON-42" in layer.content
    assert "其他会话" in layer.content  # rendered under the own-memory section


@pytest.mark.asyncio
async def test_group_layer_does_not_leak_other_agents_memory(fresh_db) -> None:
    # Agent B never recorded anything — B's layer must NOT carry A's DM fact.
    async with SessionLocal() as db:
        _, grp_id = await _setup(db)
        layer = await build_shared_memory_layer(db, grp_id, agent_id=AGENT_B)
    assert layer is None or "BLUE-FALCON-42" not in layer.content


@pytest.mark.asyncio
async def test_current_conv_rows_not_duplicated_in_own_section(fresh_db) -> None:
    # A fact recorded IN the group sits on the conv board; the own-memory
    # section must exclude it (conv_id == current conv filter).
    async with SessionLocal() as db:
        _, grp_id = await _setup(db)
        await storage_repo.add_conv_memory(
            db, conv_id=grp_id, author_agent_id=AGENT_A,
            kind="decision", content="群内决定: 用内存存储",
        )
        await db.commit()
        layer = await build_shared_memory_layer(db, grp_id, agent_id=AGENT_A)
    assert layer is not None
    assert layer.content.count("用内存存储") == 1
