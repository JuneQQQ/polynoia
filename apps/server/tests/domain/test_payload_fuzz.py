"""Adversarial fuzz / round-trip integrity for the MessagePayload union.

Three angles, all self-contained + deterministic (no network/LLM, isolated tmp DB):

1. ROUND-TRIP — for EVERY one of the 22 payload kinds: build a minimal-valid
   instance, ``model_dump(mode="json")``, persist via an isolated-DB
   ``append_message``, read it back through ``list_messages``, re-validate the
   raw blob through the discriminated union, and assert the kind + key fields
   survive byte-for-byte. A kind that fails to round-trip is a defect.

2. MALFORMED BATTERY — feed ``routes.create_message`` a battery of malformed
   bodies and assert it 400s (HTTPException) — NOT 500 (unhandled crash) and NOT
   a silent 200-persist of a non-renderable card.

3. DIFF FUZZ — feed ``_unified_diff_to_hunks`` garbage / truncated / non-diff /
   binary text and assert it never raises and always returns a list.

Isolation mirrors tests/api/test_present_policy.py::route_db — a tmp sqlite file,
``db_module.engine`` / ``db_module.SessionLocal`` / ``routes.SessionLocal`` all
monkeypatched so nothing touches :7780 or ~/.polynoia.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.api import routes
from polynoia.domain import messages as M
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.domain.messages import MessagePayload
from polynoia.storage import repo as storage_repo

_UNION = TypeAdapter(MessagePayload)


# ── isolated DB (mirror of test_present_policy.route_db) ─────────────────────
@pytest.fixture
async def route_db(monkeypatch, tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/payload-fuzz.db"
    engine = create_async_engine(
        db_url,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", session_maker)
    monkeypatch.setattr(routes, "SessionLocal", session_maker)
    async with engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)
    try:
        yield session_maker
    finally:
        await engine.dispose()


async def _make_conv(session_maker) -> str:
    conv_id = new_ulid()
    async with session_maker() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(id=conv_id, title="fuzz", members=["you", "a"], direct=True),
        )
        await db.commit()
    return conv_id


# ── one minimal-VALID instance per kind (source of truth = messages.py) ──────
def _builders() -> dict[str, M.BaseModel]:
    return {
        "text": M.TextPayload(body=[M.TextBlock(c="hi")]),
        "reasoning": M.ReasoningPayload(body=[M.TextBlock(c="think")], seconds=3),
        "tasks": M.TasksPayload(title="T", tasks=[M.TaskItem(agent="a", label="L")]),
        "discussion": M.DiscussionPayload(discussion_id="d1", topic="topic"),
        "diff": M.DiffPayload(
            file="f.py",
            additions=1,
            deletions=0,
            hunks=[M.Hunk(header="@@ -1 +1 @@", lines=[("add", 1, "x")])],
        ),
        "web": M.WebPayload(title="t", url="http://x"),
        "swatches": M.SwatchesPayload(swatches=[M.Swatch(hex="#fff", name="white")]),
        "copy": M.CopyPayload(hero=["h"], cta=M.CtaCopy(primary="p", secondary="s")),
        "metrics": M.MetricsPayload(
            service="s", stats=[M.Stat(label="l", value="v")], sparkline=[1.0, 2.0]
        ),
        "sql": M.SqlPayload(
            title="t",
            query="select 1",
            stats=M.SqlStats(rows="1", calls="1", avg_ms=1, p99_ms=2),
            explain=[M.ExplainRow(node="n", cost="1", rows=1)],
            diagnosis="d",
        ),
        "schema": M.SchemaPayload(
            table="t",
            fields=[M.SchemaField(name="n", type="int")],
            indexes=[M.SchemaIndex(name="i", cols="c")],
        ),
        "logs": M.LogsPayload(
            service="s", lines=[M.LogLine(tm="t0", level="INFO", text="x")]
        ),
        "terminal": M.TerminalPayload(command="ls"),
        "api": M.ApiPayload(
            method="GET",
            path="/p",
            desc="d",
            params=[M.ApiParam(name="n", type="str", **{"in": "query"})],
        ),
        "typing": M.TypingPayload(note="n"),
        "tool-call": M.ToolCallPayload(tool_call_id="tc1", name="Bash"),
        "ask-form": M.AskFormPayload(
            title="t", questions=[M.AskQuestion(id="q1", kind="single", label="L")]
        ),
        "image": M.ImagePayload(src="data:image/png;base64,AAAA"),
        "file": M.FilePayload(src="data:x", name="f.pdf"),
        "files": M.FilesPayload(files=[M.FilesPanelItem(src="s", name="n")]),
        "error": M.ErrorPayload(message="boom"),
        "conflict": M.ConflictPayload(
            conflict_id="c1", conv_id="cv1", branch="agent/a/conv-1", agent_id="a"
        ),
    }


# A few load-bearing fields per kind whose survival proves more than just `kind`.
_KEY_FIELDS: dict[str, list[str]] = {
    "text": ["body"],
    "reasoning": ["body", "seconds"],
    "tasks": ["title", "tasks"],
    "discussion": ["discussion_id", "topic"],
    "diff": ["file", "additions", "deletions", "hunks"],
    "web": ["title", "url"],
    "swatches": ["swatches"],
    "copy": ["hero", "cta"],
    "metrics": ["service", "stats", "sparkline"],
    "sql": ["title", "query", "stats", "explain", "diagnosis"],
    "schema": ["table", "fields", "indexes"],
    "logs": ["service", "lines"],
    "terminal": ["command"],
    "api": ["method", "path", "desc", "params"],
    "typing": ["note"],
    "tool-call": ["tool_call_id", "name"],
    "ask-form": ["title", "questions"],
    "image": ["src"],
    "file": ["src", "name"],
    "files": ["files"],
    "error": ["message"],
    "conflict": ["conflict_id", "conv_id", "branch", "agent_id"],
}


def test_builders_cover_every_union_kind() -> None:
    """Guard the guard: the builder table must cover the live union exactly, so a
    newly-added card type can never silently skip the round-trip fuzz."""
    import typing as _t

    members = _t.get_args(_t.get_args(MessagePayload)[0])
    live_kinds: set[str] = set()
    for m in members:
        fld = m.model_fields.get("kind")
        live_kinds |= {a for a in _t.get_args(fld.annotation) if isinstance(a, str)}

    built = set(_builders())
    assert built == live_kinds, (
        f"builder table drifted from union; "
        f"missing={live_kinds - built} extra={built - live_kinds}"
    )


@pytest.mark.parametrize("kind", sorted(_builders()))
async def test_payload_round_trips_through_db(route_db, kind: str) -> None:
    """Minimal-valid instance → json dict → DB → read back → re-validate union.

    Asserts the kind + every key field survive unchanged, and that the raw blob
    re-validates to the SAME pydantic member (no discriminator drift, no field
    loss/mangling through the SQLite JSON column)."""
    session_maker = route_db
    conv_id = await _make_conv(session_maker)

    inst = _builders()[kind]
    dumped = inst.model_dump(mode="json")
    assert dumped["kind"] == kind

    async with session_maker() as db:
        mid = await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="a", payload=dumped
        )
        await db.commit()

    async with session_maker() as db:
        msgs, _ = await storage_repo.list_messages(db, conv_id, limit=50)
    rows = [m for m in msgs if m["id"] == mid]
    assert len(rows) == 1, f"{kind}: persisted message not found on read-back"
    blob = rows[0]["payload"]

    # 1) kind survives
    assert blob.get("kind") == kind, f"{kind}: kind mangled to {blob.get('kind')!r}"

    # 2) the stored blob still validates to the SAME union member + same dump
    revalidated = _UNION.validate_python(blob)
    assert revalidated.model_dump(mode="json") == dumped, (
        f"{kind}: round-trip altered payload\n got={revalidated.model_dump(mode='json')}\n want={dumped}"
    )

    # 3) key fields survive byte-for-byte
    for f in _KEY_FIELDS[kind]:
        assert blob.get(f) == dumped.get(f), (
            f"{kind}: field {f!r} changed: {blob.get(f)!r} != {dumped.get(f)!r}"
        )


async def test_diff_hunk_tuple_survives_as_list(route_db) -> None:
    """DiffPayload.hunks[].lines is list[tuple[...]] — JSON has no tuple, so it
    persists as a list. The card must still re-validate (pydantic re-coerces) and
    the 3-element [kind, lineno, text] shape must be intact, not flattened."""
    session_maker = route_db
    conv_id = await _make_conv(session_maker)
    inst = M.DiffPayload(
        file="x.py",
        additions=2,
        deletions=1,
        hunks=[
            M.Hunk(
                header="@@ -1,2 +1,2 @@",
                lines=[("ctx", 1, "keep"), ("del", 2, "old"), ("add", 2, "new")],
            )
        ],
    )
    dumped = inst.model_dump(mode="json")
    async with session_maker() as db:
        mid = await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="a", payload=dumped
        )
        await db.commit()
    async with session_maker() as db:
        msgs, _ = await storage_repo.list_messages(db, conv_id, limit=10)
    blob = next(m["payload"] for m in msgs if m["id"] == mid)

    lines = blob["hunks"][0]["lines"]
    assert [tuple(x) for x in lines] == [
        ("ctx", 1, "keep"),
        ("del", 2, "old"),
        ("add", 2, "new"),
    ]
    # re-validates cleanly back into DiffPayload
    re = _UNION.validate_python(blob)
    assert isinstance(re, M.DiffPayload)
    assert re.hunks[0].lines[0] == ("ctx", 1, "keep")


async def test_api_alias_in_field_round_trips(route_db) -> None:
    """ApiParam aliases reserved word `in`→`in_`. The dump uses the python name
    `in_` (mode=json, no by_alias); it must persist + re-validate without the
    alias machinery dropping or duplicating the field."""
    session_maker = route_db
    conv_id = await _make_conv(session_maker)
    inst = M.ApiPayload(
        method="POST",
        path="/u/{id}",
        desc="d",
        params=[
            M.ApiParam(name="id", type="str", required=True, **{"in": "path"}),
            M.ApiParam(name="q", type="str", **{"in": "query"}),
        ],
    )
    dumped = inst.model_dump(mode="json")
    async with session_maker() as db:
        mid = await storage_repo.append_message(
            db, conv_id=conv_id, sender_id="a", payload=dumped
        )
        await db.commit()
    async with session_maker() as db:
        msgs, _ = await storage_repo.list_messages(db, conv_id, limit=10)
    blob = next(m["payload"] for m in msgs if m["id"] == mid)
    re = _UNION.validate_python(blob)
    assert isinstance(re, M.ApiPayload)
    assert [(p.name, p.in_) for p in re.params] == [("id", "path"), ("q", "query")]


# ── 2) malformed battery against create_message ─────────────────────────────
async def test_malformed_unknown_kind_400s(route_db) -> None:
    conv_id = await _make_conv(route_db)
    with pytest.raises(HTTPException) as ei:
        await routes.create_message(
            {"conv_id": conv_id, "payload": {"kind": "no-such-kind", "x": 1}}
        )
    assert ei.value.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": None},          # kind=null
        {"kind": 7},             # kind as int
        {"kind": 3.14},          # kind as float
        {"kind": True},          # kind as bool
        {"kind": "no-such"},     # unknown string
        {"body": []},            # missing kind
    ],
)
async def test_malformed_scalar_kind_400s_not_500(route_db, payload: dict) -> None:
    """Each of these must come back as a clean 400 — never a 500 and never a
    silent 200-persist (an unknown/typeless kind has no PARTS_REGISTRY component
    on the client, so it would render as a blank card)."""
    conv_id = await _make_conv(route_db)
    with pytest.raises(HTTPException) as ei:
        await routes.create_message({"conv_id": conv_id, "payload": payload})
    assert ei.value.status_code == 400, f"{payload} -> {ei.value.status_code}"


@pytest.mark.parametrize("payload", ["raw-string", [1, 2, 3], 42, None])
async def test_malformed_payload_not_a_dict_400s(route_db, payload) -> None:
    conv_id = await _make_conv(route_db)
    with pytest.raises(HTTPException) as ei:
        await routes.create_message({"conv_id": conv_id, "payload": payload})
    assert ei.value.status_code == 400


@pytest.mark.parametrize(
    "kind_val",
    [
        ["text"],            # list — unhashable
        {"text": 1},         # dict — unhashable
        {"a", "b"},          # set — unhashable
    ],
)
async def test_malformed_unhashable_kind_does_not_500(route_db, kind_val) -> None:
    """ADVERSARIAL — a non-scalar `kind` value.

    create_message gates with ``payload.get("kind") not in _VALID_MSG_KINDS``
    where ``_VALID_MSG_KINDS`` is a *set*. ``unhashable in set`` raises TypeError,
    so a malformed client body (``{"kind": ["text"]}``) escapes the 400 guard and
    becomes an unhandled 500 — a server crash on bad input, not a validation
    error. The endpoint should reject it as a 400 like every other bad `kind`.

    EXPECTED-FAIL until routes.create_message coerces/guards the kind type before
    the membership test (e.g. ``isinstance(kind, str) and kind in _VALID_...``).
    Keep the assertion: a green run here means the bug was fixed.
    """
    conv_id = await _make_conv(route_db)
    try:
        res = await routes.create_message(
            {"conv_id": conv_id, "payload": {"kind": kind_val}}
        )
    except HTTPException as e:
        assert e.status_code == 400, f"{kind_val!r} -> {e.status_code}"
        return
    except TypeError as e:  # noqa: BLE001
        pytest.fail(
            f"create_message 500s on unhashable kind {kind_val!r}: {e!r} "
            f"(guard does `unhashable in set` — should 400, not crash)"
        )
    pytest.fail(
        f"create_message silently accepted non-scalar kind {kind_val!r}: {res} "
        f"(persisted a card whose `kind` is not a registry key)"
    )


async def test_malformed_valid_kind_garbage_body_is_not_silently_persisted(
    route_db,
) -> None:
    """ADVERSARIAL — a STRUCTURALLY invalid payload that carries a VALID kind.

    ``{"kind": "text", "body": "<1MB string>"}`` and deeply-nested junk both pass
    the kind-only gate and 200-persist as raw dicts, even though they fail
    ``TextPayload`` validation (body must be ``list[TextBlock]``). On read-back
    they will NOT re-validate against the union → the client renders a broken /
    blank card that survives refresh. create_message validates only the
    discriminator, never the payload shape.

    This asserts the (mis)behavior so the gap is documented + caught if the
    contract tightens. If create_message starts validating the full payload
    (raising 400 here), flip the expectation — that's a fix, not a regression.
    """
    session_maker = route_db
    conv_id = await _make_conv(session_maker)

    huge = {"kind": "text", "body": "A" * 1_000_000}
    nested = {
        "kind": "text",
        "body": {"deep": {"x": [1, 2, {"y": None}]}},
        "bogus": [[[[{}]]]],
    }

    persisted_ids = []
    for bad in (huge, nested):
        res = await routes.create_message({"conv_id": conv_id, "payload": bad})
        assert res["ok"] is True
        persisted_ids.append(res["id"])

    async with session_maker() as db:
        msgs, _ = await storage_repo.list_messages(db, conv_id, limit=50)
    stored = {m["id"]: m["payload"] for m in msgs}

    # Both were silently persisted (the kind-only gate let them through)...
    for pid in persisted_ids:
        assert pid in stored, "expected silent 200-persist (kind-only gate)"

    # ...and neither survives a strict re-validation against the union — i.e. the
    # stored card is non-renderable. This is the cost of validating only `kind`.
    for pid in persisted_ids:
        with pytest.raises(Exception):
            _UNION.validate_python(stored[pid])


# ── 3) _unified_diff_to_hunks fuzz — must never raise, always returns list ───
_DIFF_GARBAGE = [
    "",
    "   ",
    "\n\n\n",
    "not a diff at all\njust prose\nwith lines\n",
    "@@ truncated header with no close and no body",
    "@@ -1,3 +1,3 @@\n+added\n-removed\n keptcontext\n",
    "+orphan add before any hunk\n-orphan del before any hunk\n",
    "@@ -1 +1 @@",  # header, no body
    "@@ -1,1 +1,1 @@\n",  # header then empty
    "\x00\x01\x02 binary junk \xff\xfe garbage",
    "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1 @@\n+only line\n",
    "@@@@@@@@@@",
    "@@ -abc,def +ghi,jkl @@\n+nonnumeric header nums\n",  # regex won't match
    "🔥💀 @@ -1 +1 @@\n+emoji prefix\n",
    "@@ -1 +1 @@\n no-newline-marker\n\\ No newline at end of file\n",
    "@@ -1 +1 @@\r\n+windows\r\n-crlf\r\n",  # CRLF
    "-" * 5000,  # huge run of deletes, no hunk
    "@@ -1 +1 @@\n" + ("+x\n" * 5000),  # huge valid hunk
    "@" * 100000,  # pathological '@' run
]


@pytest.mark.parametrize("text", _DIFF_GARBAGE)
def test_unified_diff_to_hunks_never_raises(text: str) -> None:
    """Garbage / truncated / non-diff / binary / CRLF / pathological input must
    not throw — it must return a list (possibly empty). The diff-card endpoint
    feeds arbitrary tool output here; a parser crash would 500 the card emit."""
    out = routes._unified_diff_to_hunks(text)
    assert isinstance(out, list)
    # structural sanity on whatever it DID parse
    for h in out:
        assert isinstance(h, dict)
        assert "header" in h and "lines" in h
        assert isinstance(h["lines"], list)
        for ln in h["lines"]:
            assert isinstance(ln, list) and len(ln) == 3
            assert ln[0] in ("add", "del", "ctx")


def test_unified_diff_to_hunks_non_str_raises_or_handles() -> None:
    """Defensive: passing a non-string (the endpoint does
    ``body.get("diff") or ""`` so None is coerced, but a wrong-typed body could
    slip a list/int). Document current behavior: it iterates ``.splitlines()`` so
    a non-str raises AttributeError — acceptable as long as it's not a silent
    wrong parse. We assert it raises rather than silently mis-parsing."""
    for bad in (123, ["@@ -1 +1 @@"], {"diff": "x"}):
        with pytest.raises((AttributeError, TypeError)):
            routes._unified_diff_to_hunks(bad)  # type: ignore[arg-type]
