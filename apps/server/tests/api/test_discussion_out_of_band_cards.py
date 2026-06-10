from __future__ import annotations

import pytest

from polynoia.api import routes
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import MessageRow


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "discussion-cards.db"
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url", f"sqlite+aiosqlite:///{db_path}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


async def _mk_conv(session) -> str:
    conv = Conversation(id=new_ulid(), title="discussion", members=["agent-a"])
    await storage_repo.create_conversation(session, conv)
    return conv.id


async def test_diff_and_terminal_cards_inherit_current_discussion(fresh_db):
    async with SessionLocal() as session:
        conv_id = await _mk_conv(session)
        await session.commit()

    ctx_key = f"{conv_id}:agent-a"
    routes._conv_agent_turn[ctx_key] = "turn-a"
    routes._conv_agent_discussion[ctx_key] = "disc-a"
    try:
        diff_res = await routes.post_diff_card(
            conv_id,
            {
                "sender_id": "agent-a",
                "agent_id": "agent-a",
                "file": "backend/main.py",
                "additions": 1,
                "diff": "diff --git a/backend/main.py b/backend/main.py\n"
                "--- a/backend/main.py\n"
                "+++ b/backend/main.py\n"
                "@@ -1,0 +1,1 @@\n"
                "+print('ok')\n",
                "commit_sha": "abc1234",
            },
        )
        term_res = await routes.post_terminal_card(
            conv_id,
            {
                "term_id": "term-discussion",
                "sender_id": "agent-a",
                "command": "pytest",
                "output": "ok",
                "running": False,
                "exit_code": 0,
                "mode": "blocking",
            },
        )
    finally:
        routes._conv_agent_turn.pop(ctx_key, None)
        routes._conv_agent_discussion.pop(ctx_key, None)

    async with SessionLocal() as session:
        diff_msg = await session.get(MessageRow, diff_res["id"])
        term_msg = await session.get(MessageRow, term_res["id"])

    assert diff_msg is not None
    assert term_msg is not None
    assert diff_msg.payload["turn_id"] == "turn-a"
    assert term_msg.payload["turn_id"] == "turn-a"
    assert diff_msg.payload["discussion_id"] == "disc-a"
    assert term_msg.payload["discussion_id"] == "disc-a"
