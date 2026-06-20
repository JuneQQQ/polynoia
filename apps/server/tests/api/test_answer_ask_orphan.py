from __future__ import annotations

import pytest

from polynoia.api.conversations_routes import get_open_ask_forms
from polynoia.api.routes import _pending_asks, answer_ask
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import MessageRow


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    _pending_asks.clear()
    yield
    _pending_asks.clear()


async def _seed_ask_form(conv_id: str) -> str:
    """Persist an ask-form card and return its message id (== ask_id)."""
    async with SessionLocal() as db:
        mid = await storage_repo.append_message(
            db,
            conv_id=conv_id,
            sender_id="agent-a",
            payload={
                "kind": "ask-form",
                "blocking_tool": True,
                "questions": [{"prompt": "几个?"}],
            },
        )
        await db.commit()
    return mid


@pytest.mark.asyncio
async def test_orphaned_ask_stamps_card_and_skips_user_bubble(fresh_db) -> None:
    """An ask_id absent from _pending_asks (registered before a restart) is
    orphaned: stamp the answer onto the card, return orphaned=True, and DO NOT
    persist a separate `you` message (the client re-triggers, which persists it).
    """
    conv_id = "conv-orphan"
    ask_id = await _seed_ask_form(conv_id)
    # _pending_asks is empty → orphaned.

    res = await answer_ask(conv_id, ask_id, {"answer": "大概三五百"})

    assert res["ok"] is True
    assert res["orphaned"] is True

    async with SessionLocal() as db:
        rows, _ = await storage_repo.list_messages(db, conv_id)
        card = await db.get(MessageRow, ask_id)

    # Card stamped with the answer so it reads 「已回复」.
    assert card.payload.get("answer") == "大概三五百"
    # No extra `you` text message was persisted — only the original ask-form card.
    assert [r["sender_id"] for r in rows] == ["agent-a"]


@pytest.mark.asyncio
async def test_live_ask_persists_user_bubble_no_stamp(fresh_db) -> None:
    """A registered (live) ask_id resumes the suspended turn: persist a `you`
    message so the answer survives a refresh, and do NOT stamp the card (the
    resumed tool re-broadcasts it). orphaned=False.
    """
    conv_id = "conv-live"
    ask_id = await _seed_ask_form(conv_id)
    _pending_asks[ask_id] = None  # register_ask seeds this for a live turn.

    res = await answer_ask(conv_id, ask_id, {"answer": "两三百"})

    assert res["ok"] is True
    assert res["orphaned"] is False
    # The poll loop will consume the stored answer.
    assert _pending_asks[ask_id] == "两三百"

    async with SessionLocal() as db:
        rows, _ = await storage_repo.list_messages(db, conv_id)
        card = await db.get(MessageRow, ask_id)

    # A `you` bubble was persisted (refresh-survival); card NOT stamped.
    senders = [r["sender_id"] for r in rows]
    assert senders == ["agent-a", "you"]
    assert "answer" not in card.payload


@pytest.mark.asyncio
async def test_open_ask_forms_excludes_answered_blocking_form(fresh_db) -> None:
    """A blocking ask whose card is stamped (answer/answered) must NOT re-hydrate
    as open on refresh. Regression: the orphaned-recovery path stamps the card
    but writes NO `you` message, so the `i > last_user_idx` heuristic alone left
    it "open" and the panel resurrected, asking the user to answer again.
    """
    conv_id = "conv-rehydrate"
    ask_id = await _seed_ask_form(conv_id)  # no _pending_asks entry → orphaned
    await answer_ask(conv_id, ask_id, {"answer": "Excel 表格"})

    res = await get_open_ask_forms(conv_id)
    assert res["ask_forms"] == []  # answered → not re-hydrated


@pytest.mark.asyncio
async def test_open_ask_forms_rehydrates_unanswered_form(fresh_db) -> None:
    """An UNanswered blocking ask DOES re-hydrate so a refresh re-shows the panel."""
    conv_id = "conv-open"
    ask_id = await _seed_ask_form(conv_id)

    res = await get_open_ask_forms(conv_id)
    assert [f["id"] for f in res["ask_forms"]] == [ask_id]
