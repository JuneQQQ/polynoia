from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from polynoia.api import routes
from polynoia.api.conversations_routes import get_open_ask_forms
from polynoia.api.execution import RUNTIME
from polynoia.api.routes import _ask_conv, _pending_asks, answer_ask, poll_ask
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
    _ask_conv.clear()
    yield
    _pending_asks.clear()
    _ask_conv.clear()


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
async def test_orphaned_answer_remains_retryable_until_durable_resume(fresh_db) -> None:
    """An orphan has no poller, so POST must not manufacture a live registry.

    The browser resumes it through the receipt-backed ordinary-message outbox.
    If that handoff cannot be enqueued, retrying this REST call must still report
    ``orphaned=True`` instead of pretending a suspended turn consumed the answer.
    """
    conv_id = "conv-orphan-retry"
    ask_id = await _seed_ask_form(conv_id)

    first = await answer_ask(conv_id, ask_id, {"answer": "保留这份答案"})
    second = await answer_ask(conv_id, ask_id, {"answer": "保留这份答案"})

    assert first == {"ok": True, "orphaned": True}
    assert second == {"ok": True, "orphaned": True}
    assert ask_id not in _pending_asks
    assert ask_id not in _ask_conv

    async with SessionLocal() as db:
        rows, _ = await storage_repo.list_messages(db, conv_id)
        card = await db.get(MessageRow, ask_id)

    assert [row["sender_id"] for row in rows] == ["agent-a"]
    assert card is not None
    assert card.payload.get("answer") == "保留这份答案"


@pytest.mark.asyncio
async def test_live_ask_persists_user_bubble_no_stamp(fresh_db) -> None:
    """A registered (live) ask_id resumes the suspended turn: persist a `you`
    message so the answer survives a refresh, and do NOT stamp the card (the
    resumed tool re-broadcasts it). orphaned=False.
    """
    conv_id = "conv-live"
    ask_id = await _seed_ask_form(conv_id)
    _pending_asks[ask_id] = None  # register_ask seeds both live registries.
    _ask_conv[ask_id] = conv_id

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
async def test_live_answer_is_idempotent_before_and_after_poll(fresh_db) -> None:
    conv_id = "conv-live-retry"
    ask_id = await _seed_ask_form(conv_id)
    _pending_asks[ask_id] = None
    _ask_conv[ask_id] = conv_id

    first = await answer_ask(conv_id, ask_id, {"answer": "唯一答案"})
    replay_before_poll = await answer_ask(
        conv_id, ask_id, {"answer": "唯一答案"}
    )
    delivered = await poll_ask(conv_id, ask_id)
    assert ask_id not in _pending_asks
    assert ask_id not in _ask_conv
    delivered_retry = await poll_ask(conv_id, ask_id)
    replay_after_poll = await answer_ask(
        conv_id, ask_id, {"answer": "唯一答案"}
    )

    assert first == {"ok": True, "orphaned": False}
    assert replay_before_poll == first
    assert delivered == {"answered": True, "answer": "唯一答案"}
    assert delivered_retry == delivered
    assert replay_after_poll == first

    async with SessionLocal() as db:
        rows, _ = await storage_repo.list_messages(db, conv_id)
        answer_row = await db.get(MessageRow, routes._ask_answer_message_id(ask_id))

    you_rows = [row for row in rows if row["sender_id"] == "you"]
    assert len(you_rows) == 1
    assert you_rows[0]["in_reply_to"] == ask_id
    assert answer_row is not None
    assert answer_row.payload.get("_ask_answer_polled") is True


@pytest.mark.asyncio
async def test_unpolled_live_answer_becomes_retryable_orphan_after_registry_loss(
    fresh_db,
) -> None:
    conv_id = "conv-live-crash-window"
    ask_id = await _seed_ask_form(conv_id)
    _pending_asks[ask_id] = None
    _ask_conv[ask_id] = conv_id

    first = await answer_ask(conv_id, ask_id, {"answer": "survive restart"})
    _pending_asks.clear()
    _ask_conv.clear()
    replay = await answer_ask(conv_id, ask_id, {"answer": "survive restart"})

    assert first == {"ok": True, "orphaned": False}
    assert replay == {"ok": True, "orphaned": True}
    async with SessionLocal() as db:
        answer_row = await db.get(MessageRow, routes._ask_answer_message_id(ask_id))
        card = await db.get(MessageRow, ask_id)
    assert answer_row is None
    assert card is not None
    assert card.payload.get("answer") == "survive restart"


@pytest.mark.asyncio
async def test_poll_and_answer_retry_make_one_atomic_handoff(
    fresh_db, monkeypatch
) -> None:
    """A retry cannot turn an answer into an orphan after poll claimed it.

    The gate stops poll after it removes the live maps but before it marks the
    durable answer consumed. Historically a concurrent POST observed that
    split state, deleted the answer row, and started a second replacement turn.
    """
    conv_id = "conv-live-poll-race"
    ask_id = await _seed_ask_form(conv_id)
    _pending_asks[ask_id] = None
    _ask_conv[ask_id] = conv_id
    await answer_ask(conv_id, ask_id, {"answer": "one handoff"})

    answer_msg_id = routes._ask_answer_message_id(ask_id)
    entered = asyncio.Event()
    release = asyncio.Event()
    original_get = routes.SessionLocal.class_.get
    gated = False

    async def gated_get(session, entity, ident, *args, **kwargs):
        nonlocal gated
        current = asyncio.current_task()
        if (
            not gated
            and current is not None
            and current.get_name() == "poller"
            and entity is MessageRow
            and ident == answer_msg_id
        ):
            gated = True
            entered.set()
            await release.wait()
        return await original_get(session, entity, ident, *args, **kwargs)

    monkeypatch.setattr(routes.SessionLocal.class_, "get", gated_get)
    poll_task = asyncio.create_task(poll_ask(conv_id, ask_id), name="poller")
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    retry_task = asyncio.create_task(
        answer_ask(conv_id, ask_id, {"answer": "one handoff"})
    )
    await asyncio.sleep(0)
    assert not retry_task.done()

    release.set()
    polled, retried = await asyncio.gather(poll_task, retry_task)

    assert polled == {"answered": True, "answer": "one handoff"}
    assert retried == {"ok": True, "orphaned": False}
    assert ask_id not in _pending_asks
    assert ask_id not in _ask_conv
    async with SessionLocal() as db:
        answer_row = await db.get(MessageRow, answer_msg_id)
        card = await db.get(MessageRow, ask_id)
    assert answer_row is not None
    assert answer_row.payload.get("_ask_answer_polled") is True
    assert card is not None
    assert "answer" not in card.payload
    assert conv_id not in RUNTIME.user_message_locks


@pytest.mark.asyncio
async def test_concurrent_live_answer_retries_share_one_durable_claim(
    fresh_db,
) -> None:
    conv_id = "conv-live-concurrent"
    ask_id = await _seed_ask_form(conv_id)
    _pending_asks[ask_id] = None
    _ask_conv[ask_id] = conv_id

    results = await asyncio.gather(
        *(answer_ask(conv_id, ask_id, {"answer": "same"}) for _ in range(8))
    )

    assert results == [{"ok": True, "orphaned": False}] * 8
    assert _pending_asks[ask_id] == "same"
    async with SessionLocal() as db:
        rows, _ = await storage_repo.list_messages(db, conv_id)
    assert len([row for row in rows if row["sender_id"] == "you"]) == 1


@pytest.mark.asyncio
async def test_live_answer_rejects_different_replay(fresh_db) -> None:
    conv_id = "conv-live-conflict"
    ask_id = await _seed_ask_form(conv_id)
    _pending_asks[ask_id] = None
    _ask_conv[ask_id] = conv_id
    await answer_ask(conv_id, ask_id, {"answer": "first"})

    with pytest.raises(HTTPException) as exc_info:
        await answer_ask(conv_id, ask_id, {"answer": "different"})

    assert exc_info.value.status_code == 409
    assert _pending_asks[ask_id] == "first"


@pytest.mark.asyncio
async def test_live_ask_cannot_be_answered_or_polled_through_another_conv(
    fresh_db,
) -> None:
    owner_conv = "conv-owner"
    wrong_conv = "conv-wrong"
    ask_id = await _seed_ask_form(owner_conv)
    _pending_asks[ask_id] = None
    _ask_conv[ask_id] = owner_conv

    with pytest.raises(HTTPException) as answer_error:
        await answer_ask(wrong_conv, ask_id, {"answer": "stolen"})
    with pytest.raises(HTTPException) as poll_error:
        await poll_ask(wrong_conv, ask_id)

    assert answer_error.value.status_code == 404
    assert poll_error.value.status_code == 404
    assert _pending_asks[ask_id] is None
    assert _ask_conv[ask_id] == owner_conv

    async with SessionLocal() as db:
        owner_rows, _ = await storage_repo.list_messages(db, owner_conv)
        wrong_rows, _ = await storage_repo.list_messages(db, wrong_conv)

    assert [row["sender_id"] for row in owner_rows] == ["agent-a"]
    assert wrong_rows == []


@pytest.mark.asyncio
async def test_orphaned_ask_card_is_bound_to_its_conversation(fresh_db) -> None:
    owner_conv = "conv-orphan-owner"
    ask_id = await _seed_ask_form(owner_conv)

    with pytest.raises(HTTPException) as exc_info:
        await answer_ask("conv-orphan-wrong", ask_id, {"answer": "stolen"})

    assert exc_info.value.status_code == 404
    assert ask_id not in _pending_asks
    async with SessionLocal() as db:
        card = await db.get(MessageRow, ask_id)
    assert card is not None
    assert "answer" not in card.payload


@pytest.mark.asyncio
async def test_orphaned_answer_rejects_a_different_replay(fresh_db) -> None:
    conv_id = "conv-orphan-conflict"
    ask_id = await _seed_ask_form(conv_id)
    await answer_ask(conv_id, ask_id, {"answer": "first"})

    with pytest.raises(HTTPException) as exc_info:
        await answer_ask(conv_id, ask_id, {"answer": "different"})

    assert exc_info.value.status_code == 409
    async with SessionLocal() as db:
        card = await db.get(MessageRow, ask_id)
    assert card is not None
    assert card.payload.get("answer") == "first"


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


@pytest.mark.asyncio
async def test_open_ask_ids_and_orphan_conv_asks(fresh_db) -> None:
    """open_ask_ids lists a conv's UNanswered asks; orphan_conv_asks drops the
    new ones (so a later answer is treated as orphaned → fresh re-trigger), while
    keeping pre-existing asks and never touching other convs / answered asks.
    """
    from polynoia.api.routes import _ask_conv, open_ask_ids, orphan_conv_asks

    _pending_asks.clear()
    _ask_conv.clear()
    conv, other = "conv-A", "conv-B"
    _pending_asks["a1"], _ask_conv["a1"] = None, conv        # open, this conv
    _pending_asks["a2"], _ask_conv["a2"] = "answered", conv  # answered → not open
    _pending_asks["a3"], _ask_conv["a3"] = None, other       # open, other conv

    assert open_ask_ids(conv) == {"a1"}  # only unanswered + this conv

    # A new ask appears during a turn (a4); a1 was already open at turn start.
    _pending_asks["a4"], _ask_conv["a4"] = None, conv
    dropped = orphan_conv_asks(conv, keep={"a1"})
    assert dropped == ["a4"]                       # only the new one
    assert "a4" not in _pending_asks and "a4" not in _ask_conv
    assert "a1" in _pending_asks                   # kept (pre-existing)
    assert "a2" in _pending_asks and "a3" in _pending_asks  # answered / other conv untouched

    # An orphaned ask id is exactly what answer_ask treats as orphaned.
    assert "a4" not in _pending_asks  # → answer_ask(...) would return orphaned=True

    _pending_asks.clear()
    _ask_conv.clear()
