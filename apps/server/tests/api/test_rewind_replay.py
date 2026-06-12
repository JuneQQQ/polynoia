"""Adversarial REWIND / REPLAY / IDEMPOTENCY tests (isolated tmp DB).

Angle: pick the orderings / duplicate-id / same-timestamp races most likely to
expose latent defects in the rewind-to-here + optimistic-msg_id paths.

Targets (all real production symbols, read-only):
  - storage/repo/messages.py: append_message / delete_messages_from /
    upsert_message / list_messages
  - api/routes.py: rewind_conversation / create_message

KNOWN LOW BUG under test (scenario 1): `delete_messages_from` cuts by
``created_at >= cutoff`` (see messages.py:92-105), NOT by ULID. Two messages
written in the SAME millisecond share a ``created_at``; rewinding from the
*later* ULID therefore also deletes the *earlier*, still-wanted sibling. The
correct, deterministic boundary is ULID order (which `list_messages` itself
tie-breaks on, id.desc()). We assert the ULID-correct outcome and let it FAIL
if the code uses the ambiguous created_at boundary — that failure is the win.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from polynoia.api import routes
from polynoia.api.routes import create_message, rewind_conversation
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine
from polynoia.storage.models import MessageRow


@pytest.fixture
async def fresh_db(tmp_path, monkeypatch):
    """Isolated tmp sqlite — mirrors tests/api/test_rewind.py::fresh_db. Never
    touches ~/.polynoia or the live server DB."""
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url",
        f"sqlite+aiosqlite:///{tmp_path / 'rewind_replay.db'}",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


def _text(c: str) -> dict:
    return {"kind": "text", "body": [{"t": "p", "c": c}]}


async def _mk_conv(conv_id: str, members: list[str] | None = None) -> None:
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="dm",
                members=members or ["you", "agent-a"],
                direct=True,
                group=False,
            ),
        )
        await db.commit()


# ── (1) Same-millisecond created_at, distinct ULIDs → ULID-deterministic ─────


@pytest.mark.asyncio
async def test_rewind_same_millisecond_keeps_earlier_ulid(fresh_db) -> None:
    """Two messages with the IDENTICAL created_at but ULID order m_lo < m_hi.

    Rewinding "from m_hi" must delete ONLY m_hi (and anything after it by ULID),
    leaving m_lo — the earlier message by ULID — intact. If `delete_messages_from`
    keys on created_at (the known bug) it deletes BOTH (m_lo.created_at >= cutoff
    is also true), wiping a message the user wanted to keep. We assert the
    ULID-correct survivor and KEEP the failing assertion if the code is wrong.
    """
    conv_id = new_ulid()
    await _mk_conv(conv_id)

    same_ts = datetime(2026, 6, 11, 8, 30, 0, 123000)  # one fixed instant
    # ULIDs are lexically sortable; force a strict lo < hi pair.
    ids = sorted(new_ulid() for _ in range(2))
    m_lo, m_hi = ids[0], ids[1]
    assert m_lo < m_hi

    async with SessionLocal() as db:
        db.add(MessageRow(id=m_lo, conv_id=conv_id, sender_id="you",
                          payload=_text("keep me"), created_at=same_ts))
        db.add(MessageRow(id=m_hi, conv_id=conv_id, sender_id="agent-a",
                          payload=_text("rewind from here"), created_at=same_ts))
        await db.commit()

    res = await rewind_conversation(conv_id, {"from_msg_id": m_hi})
    assert res["ok"] is True

    async with SessionLocal() as db:
        remaining, _ = await storage_repo.list_messages(db, conv_id)
    remaining_ids = [m["id"] for m in remaining]

    # Correct ULID-boundary semantics: only the rewound-from message is gone.
    assert remaining_ids == [m_lo], (
        "rewind cut by created_at, not ULID: the earlier same-millisecond "
        f"message {m_lo} was wrongly deleted (remaining={remaining_ids}). "
        "This is the known low bug — delete_messages_from uses created_at>=cutoff."
    )
    assert res["deleted"] == 1, (
        f"expected to delete exactly m_hi, got deleted={res['deleted']} — "
        "the same-millisecond sibling was swept by the created_at boundary."
    )


@pytest.mark.asyncio
async def test_list_messages_ordering_is_ulid_deterministic(fresh_db) -> None:
    """Ordering of same-millisecond rows must be stable & ULID-ascending across
    reads (list_messages tie-breaks id.desc() then reverses → ascending id).
    This is the *non-buggy* sibling read path; it must not be ambiguous."""
    conv_id = new_ulid()
    await _mk_conv(conv_id)

    same_ts = datetime(2026, 6, 11, 9, 0, 0, 500000)
    ids = sorted(new_ulid() for _ in range(4))
    async with SessionLocal() as db:
        # insert in REVERSE ULID order to prove ordering isn't insertion order
        for mid in reversed(ids):
            db.add(MessageRow(id=mid, conv_id=conv_id, sender_id="you",
                              payload=_text(mid), created_at=same_ts))
        await db.commit()

    async with SessionLocal() as db:
        msgs1, _ = await storage_repo.list_messages(db, conv_id)
    async with SessionLocal() as db:
        msgs2, _ = await storage_repo.list_messages(db, conv_id)

    assert [m["id"] for m in msgs1] == ids, "same-ms order must be ULID-ascending"
    assert [m["id"] for m in msgs1] == [m["id"] for m in msgs2], (
        "ordering must be deterministic across reads, not SQLite rowid-arbitrary"
    )


# ── (2) Optimistic msg_id idempotency: same id twice → ONE row, no crash ─────


@pytest.mark.asyncio
async def test_create_message_same_optimistic_id_is_idempotent(fresh_db) -> None:
    """A client retry / double-send replays the SAME pre-allocated ``msg_id``.

    create_message → append_message must not produce two rows or raise on the
    primary-key collision. The intended contract (per create_message's docstring
    + upsert_message existing right beside it) is a clean idempotent write: one
    row survives. append_message does a raw session.add(MessageRow(id=msg_id)),
    so the second call collides on the PK. We assert idempotency and KEEP the
    failure (IntegrityError or a duplicate row) if the code does not dedup.
    """
    conv_id = new_ulid()
    await _mk_conv(conv_id)

    opt_id = new_ulid()
    body = {"conv_id": conv_id, "sender_id": "you", "payload": _text("hi"),
            "msg_id": opt_id}

    r1 = await create_message(body)
    assert r1["id"] == opt_id

    crashed: Exception | None = None
    try:
        r2 = await create_message(dict(body))  # exact replay
    except (IntegrityError, Exception) as exc:  # noqa: BLE001 - capture for assert
        crashed = exc

    async with SessionLocal() as db:
        msgs, _ = await storage_repo.list_messages(db, conv_id)
    rows_with_id = [m for m in msgs if m["id"] == opt_id]

    assert crashed is None, (
        "create_message is NOT idempotent on a replayed optimistic msg_id: "
        f"second call raised {type(crashed).__name__}: {crashed}. "
        "append_message does a bare INSERT on the supplied id instead of an upsert."
    )
    assert len(rows_with_id) == 1, (
        f"expected exactly one row for the optimistic id, got {len(rows_with_id)} "
        "— a replayed msg_id produced a duplicate."
    )
    assert r2["id"] == opt_id


# ── (3) Rewind to a step, then append → no id collision, no resurrected rows ──


@pytest.mark.asyncio
async def test_rewind_then_append_no_collision(fresh_db) -> None:
    """Append → rewind to step 2 → append again. The rewound-past messages are
    gone and the new message neither collides with a deleted id nor resurrects
    deleted rows."""
    conv_id = new_ulid()
    await _mk_conv(conv_id)

    ids: list[str] = []
    async with SessionLocal() as db:
        for i in range(4):
            mid = await storage_repo.append_message(
                db, conv_id=conv_id, sender_id="you", payload=_text(f"m{i}"))
            ids.append(mid)
        await db.commit()

    cut = ids[2]  # rewind from the 3rd message → m2, m3 deleted
    res = await rewind_conversation(conv_id, {"from_msg_id": cut})
    assert res["ok"] is True
    assert res["deleted"] == 2, f"expected m2+m3 deleted, got {res['deleted']}"

    async with SessionLocal() as db:
        after_rewind, _ = await storage_repo.list_messages(db, conv_id)
    assert [m["id"] for m in after_rewind] == ids[:2]

    # Re-send: a fresh ULID after rewind must sort AFTER the survivors and not
    # reuse a deleted id.
    async with SessionLocal() as db:
        new_mid = await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="you", payload=_text("replay"))
        await db.commit()
    assert new_mid not in ids[2:], "new message reused a rewound-away id"
    assert new_mid > ids[1], "replayed message must sort after the survivors"

    async with SessionLocal() as db:
        final, _ = await storage_repo.list_messages(db, conv_id)
    final_ids = [m["id"] for m in final]
    assert final_ids == [*ids[:2], new_mid]
    assert cut not in final_ids and ids[3] not in final_ids, (
        "a rewound-past message resurfaced after the replay append"
    )


# ── (4) Reply to a message that was rewound/deleted → graceful read ──────────


@pytest.mark.asyncio
async def test_reply_to_rewound_message_reads_gracefully(fresh_db) -> None:
    """A reply (in_reply_to) points at a target that is later rewound away.

    The dangling in_reply_to (no FK by design — see MessageRow comment) must not
    crash list_messages / rewind. The reply itself survives (it's older than the
    cutoff) carrying a now-orphaned in_reply_to; reading it must be graceful.
    """
    conv_id = new_ulid()
    await _mk_conv(conv_id)

    async with SessionLocal() as db:
        a = await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="you", payload=_text("question"))
        reply = await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="agent-a", payload=_text("answer"),
            in_reply_to=a)
        later = await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="you", payload=_text("followup"),
            in_reply_to=reply)
        await db.commit()

    # Rewind from `later` (the message that replies to `reply`): `later` is gone,
    # but `reply` (which `later` referenced) stays and now... still fine.
    # Then ALSO rewind from `reply` to orphan `later`'s reference — but later is
    # already gone. Instead orphan it the other way: delete `a`'s thread by
    # rewinding from `reply`, leaving... nothing references it. So craft the true
    # dangling case: rewind from `a` itself would delete the whole thread. To get
    # a SURVIVING message with a dangling in_reply_to we rewind from `reply`,
    # which deletes reply+later but keeps `a`. That leaves no dangling ref.
    #
    # The genuine dangling case: a message replies FORWARD-safe but its target is
    # individually deletable. delete_message removes a single row by id; do that
    # to the target `a`, leaving `reply` with in_reply_to=a pointing at a hole.
    async with SessionLocal() as db:
        ok = await storage_repo.delete_message(db, a)
        await db.commit()
    assert ok is True

    # Reading must not crash and the orphaned reply must still be returned with
    # its (now-dangling) in_reply_to preserved verbatim — no resolution attempt.
    async with SessionLocal() as db:
        msgs, _ = await storage_repo.list_messages(db, conv_id)
    by_id = {m["id"]: m for m in msgs}
    assert a not in by_id, "target was deleted"
    assert reply in by_id, "the reply survives the target's deletion"
    assert by_id[reply]["in_reply_to"] == a, (
        "dangling in_reply_to must be preserved verbatim (no FK, no silent null)"
    )
    assert by_id[later]["in_reply_to"] == reply

    # And a rewind that targets the now-orphan-referencing message is still fine.
    res = await rewind_conversation(conv_id, {"from_msg_id": reply})
    assert res["ok"] is True
    async with SessionLocal() as db:
        final, _ = await storage_repo.list_messages(db, conv_id)
    assert [m["id"] for m in final] == []  # reply + later both >= cutoff


# ── (5) Rewind targeting a foreign / missing id is a clean no-op vs 404 ───────


@pytest.mark.asyncio
async def test_rewind_unknown_id_does_not_touch_other_conv(fresh_db, monkeypatch) -> None:
    """Rewinding a conv with a from_msg_id that belongs to a DIFFERENT conv must
    404 and delete NOTHING — never cross conv boundaries via the created_at
    cutoff. Guards against a cross-conversation data-loss race."""
    # routes.rewind uses module-level SessionLocal; fresh_db already points it at
    # the tmp DB (same SessionLocal object), so no extra patch needed.
    conv_a = new_ulid()
    conv_b = new_ulid()
    await _mk_conv(conv_a)
    await _mk_conv(conv_b)

    async with SessionLocal() as db:
        b_mid = await storage_repo.append_message(
            db, conv_id=conv_b, sender_id="you", payload=_text("in B"))
        a_mid = await storage_repo.append_message(
            db, conv_id=conv_a, sender_id="you", payload=_text("in A"))
        await db.commit()

    # Ask conv_a to rewind from a message that lives in conv_b → must 404.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        await rewind_conversation(conv_a, {"from_msg_id": b_mid})
    assert ei.value.status_code == 404

    # Nothing deleted in either conv.
    async with SessionLocal() as db:
        a_msgs, _ = await storage_repo.list_messages(db, conv_a)
        b_msgs, _ = await storage_repo.list_messages(db, conv_b)
    assert [m["id"] for m in a_msgs] == [a_mid]
    assert [m["id"] for m in b_msgs] == [b_mid]
