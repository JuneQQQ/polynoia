"""Adversarial coverage for the CONFLICT double-spawn guard (GAP TG3).

``_drain_unmerged_branches`` (ws_conv.py) is the single merge code path. Its
ONLY guard against duplicate conflict rows / cards and duplicate auto-fix
spawns is the ``already`` skip near ws_conv.py:813-831::

    already = {
        r.branch
        for r in await storage_repo.list_conflicts(_db, conv_id)
        if r.status in ("open", "resolving", "abandoned")
    }
    ...
    for b in ...:
        if b in already:
            continue

Two facts make this load-bearing, and both are exercised here against the REAL
primitives (no mocks of the repo or the merge engine):

  1. ``Sandbox.probe_merge`` is TRANSIENT — on a conflict it ``git merge
     --abort``s and leaves the shared root clean+mergeable, so a SECOND drain
     re-probes the SAME branch and gets "conflict" AGAIN. Verified live below.

  2. ``storage_repo.create_conflict`` is NOT idempotent — it always inserts a
     fresh ULID row. So nothing downstream dedups; if the ``already`` skip were
     dropped, every drain would mint a new ConflictRow + a new ``conflict`` card
     + fire another auto-fix spawn for the same unmerged branch.

Why a harness, not the closure itself: ``_drain_unmerged_branches`` /
``_surface_conflict`` / the ``_resolver`` selection are nested closures defined
INSIDE the ``/ws/conv/{conv_id}`` WebSocket handler (capturing ``conv_id``,
``emit``, ``_spawn_turn``, ``run_adapter_turn`` from the enclosing scope) and
are not importable. Per the task's "test the smallest real unit rather than
mocking everything", ``_DrainHarness`` below re-expresses the conflict branch of
that closure VERBATIM (the ``already`` set comprehension + the ``_surface``
persistence + the ``_resolver`` ternary, source lines 813-914) and drives it
against the real Sandbox, the real storage repo, and the real ConflictPayload.
``_spawn_turn`` is the only seam replaced (by a recorder) so we can observe WHO
the drain would spawn without launching a real adapter turn. If the guard
predicate in source drifts from this copy, these tests stop reflecting reality —
they are intentionally a thin, faithful mirror, not an independent re-design.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from polynoia.domain.entities import Conversation, new_ulid
from polynoia.domain.messages import ConflictFile, ConflictPayload
from polynoia.sandbox._core import Sandbox
from polynoia.storage import repo as storage_repo
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import Base, SessionLocal, engine


# ── isolated env: fresh DB + sandbox root pinned to tmp (mirrors test_conflict_flow.env) ──
@pytest.fixture
async def env(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(
        "polynoia.settings.settings.db_url", f"sqlite+aiosqlite:///{db_path}"
    )
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sb")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await bootstrap_db()
    yield


def _commit(cwd: Path, path: str, content: str, msg: str) -> None:
    (Path(cwd) / path).write_text(content)
    subprocess.run(["git", "add", path], cwd=cwd, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", msg], cwd=cwd, check=True, capture_output=True
    )


async def _build_content_conflict(ws_id: str, conv_id: str):
    """Seed a real branch that conflicts with a b-inclusive main.

    Returns (branch, captured_files, workspace_root). Identical construction to
    test_conflict_flow so the conflict detail is the real probe_merge output.
    """
    a = await Sandbox.create_workspace_sandbox(
        workspace_id=ws_id, conv_id=conv_id, agent_id="ag-A"
    )
    root = a.workspace_root
    assert root is not None
    _commit(root, "f.txt", "L1\nBASE\nL3\n", "base f")
    b = await Sandbox.create_workspace_sandbox(
        workspace_id=ws_id, conv_id=conv_id, agent_id="ag-B"
    )
    d = await Sandbox.create_workspace_sandbox(
        workspace_id=ws_id, conv_id=conv_id, agent_id="ag-D"
    )
    _commit(b.root, "f.txt", "L1\nB-SIDE\nL3\n", "b edits")
    _commit(d.root, "f.txt", "L1\nD-SIDE\nL3\n", "d edits")
    # b merges clean (advances main); d now genuinely conflicts.
    assert (await b.probe_merge(b.branch))[0] == "clean"
    status, detail = await d.probe_merge(d.branch)
    assert status == "conflict"
    return d.branch, detail["files"], root


class _DrainHarness:
    """Faithful re-expression of ``_drain_unmerged_branches``' conflict branch
    (ws_conv.py ~813-914), parameterised so a test can run it repeatedly.

    Drives the REAL Sandbox merge engine + REAL storage repo. The only seam is
    ``_spawn_turn`` → ``self.spawns.append((agent_id, ...))`` so the resolver
    target is observable without launching an adapter. ``_surface_conflict`` is
    inlined here exactly as the source persists it (ConflictRow + conflict card
    message), since the real one is an unimportable closure.
    """

    def __init__(self, conv_id: str, ws_id: str):
        self.conv_id = conv_id
        self.ws_id = ws_id
        self.spawns: list[tuple[str, str]] = []  # (resolver_agent_id, branch)
        self.cards_emitted: list[str] = []  # card_msg_ids broadcast

    async def _surface_conflict(self, db, branch, author, files, orch_id, base_agents):
        # VERBATIM persistence from _surface_conflict (ws_conv.py:622-655):
        # one ConflictRow + one `conflict` card message keyed by card_msg_id.
        import uuid

        card_msg_id = f"conflict-{uuid.uuid4().hex[:12]}"
        cid = await storage_repo.create_conflict(
            db, conv_id=self.conv_id, workspace_id=self.ws_id, branch=branch,
            agent_id=author, files=files, card_msg_id=card_msg_id,
            base_agents=base_agents,
        )
        crow = await storage_repo.get_conflict(db, cid)
        payload = ConflictPayload(
            conflict_id=cid, conv_id=self.conv_id, branch=branch, agent_id=author,
            base_agents=base_agents, status="open",
            files=[ConflictFile(**f) for f in files],
            created_at=crow.created_at if crow else None,
        ).model_dump(mode="json")
        await storage_repo.append_message(
            db, conv_id=self.conv_id, sender_id=orch_id,
            payload=payload, msg_id=card_msg_id,
        )
        self.cards_emitted.append(card_msg_id)
        return cid

    async def drain(self, orch_id: str = "orchestrator"):
        """One pass of the drain over every agent branch ahead of main.

        Mirrors ws_conv.py: build the ``already`` skip set, then for each branch
        probe_merge → on conflict surface ONE row+card and pick the resolver. The
        skip predicate + the resolver ternary are copied from source so a guard
        regression there is reproduced here.
        """
        ws_sandbox = Sandbox.open_workspace_if_exists(self.ws_id)
        assert ws_sandbox is not None
        from polynoia.sandbox import workspace_merge_lock

        async with workspace_merge_lock(self.ws_id):
            async with SessionLocal() as _db:
                _conv = await storage_repo.get_conversation(_db, self.conv_id)
                # ── THE GUARD (ws_conv.py:815-819) ──
                already = {
                    r.branch
                    for r in await storage_repo.list_conflicts(_db, self.conv_id)
                    if r.status in ("open", "resolving", "abandoned")
                }
            merged_authors: list[str] = []
            for b in await ws_sandbox.list_agent_branches(conv_id=self.conv_id):
                if b in already:
                    continue
                if await ws_sandbox.branch_ahead_of_main(b) <= 0:
                    continue
                status, detail = await ws_sandbox.probe_merge(b)
                author = b.split("/")[1] if "/" in b else b
                if status == "clean":
                    merged_authors.append(author)
                elif status == "conflict":
                    files = detail.get("files", [])
                    async with SessionLocal() as _db2:
                        cid = await self._surface_conflict(
                            _db2, b, author, files, orch_id,
                            base_agents=list(merged_authors),
                        )
                        await _db2.commit()
                    # ── THE RESOLVER SELECTION (ws_conv.py:895-914) ──
                    _true_orch = bool(
                        orch_id
                        and _conv
                        and orch_id == getattr(_conv, "orchestrator_member_id", None)
                    )
                    _has_orch = bool(
                        _conv and getattr(_conv, "orchestrator_member_id", None)
                    )
                    _resolver = (
                        orch_id if _true_orch
                        else author if (cid and _conv and not _has_orch)
                        else None
                    )
                    if _resolver and _conv and _conv.merge_mode == "auto":
                        # source spawns run_adapter_turn(_resolver, ...) here.
                        self.spawns.append((_resolver, b))


async def _seed_conv(conv_id: str, *, group: bool, orch: str | None, merge_mode="auto"):
    async with SessionLocal() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id,
                title="t",
                members=["you", "ag-D"] + ([orch] if orch else []),
                group=group,
                direct=not group,
                orchestrator_member_id=orch,
                merge_mode=merge_mode,
            ),
        )
        await db.commit()


# ── (0) sanity: probe_merge really is transient (re-probe still conflicts) ──
@pytest.mark.asyncio
async def test_probe_merge_is_transient_so_redrain_would_reconflict(env):
    """The premise of the whole guard: a conflicting branch is NOT consumed by
    probing. Without this, a second drain couldn't double-spawn and the guard
    would be pointless. Asserts the live merge engine re-yields 'conflict'."""
    conv_id, ws_id = new_ulid(), "wsTRANS"
    await _seed_conv(conv_id, group=True, orch="ag-orch")
    branch, _files, _root = await _build_content_conflict(ws_id, conv_id)
    sb = Sandbox.open_workspace_if_exists(ws_id)
    assert sb is not None
    from polynoia.sandbox import workspace_merge_lock

    async with workspace_merge_lock(ws_id):
        assert (await sb.probe_merge(branch))[0] == "conflict"
        # Re-probe: still conflict, root still clean → a naive re-drain WOULD
        # re-surface. The `already` skip is the only thing that prevents it.
        assert (await sb.probe_merge(branch))[0] == "conflict"


# ── (1) same conflict surfaced twice → exactly ONE row + ONE card ──
@pytest.mark.asyncio
async def test_double_drain_yields_single_conflict_row_and_card(env):
    """Scenario (1): drain the SAME unmerged conflicting branch twice. The
    `already` skip must make the second drain a no-op → exactly one ConflictRow,
    one `conflict` card message, and one auto-fix spawn (never two)."""
    conv_id, ws_id = new_ulid(), "wsDUP"
    await _seed_conv(conv_id, group=True, orch="ag-orch")
    _branch, _files, _root = await _build_content_conflict(ws_id, conv_id)

    h = _DrainHarness(conv_id, ws_id)
    await h.drain(orch_id="ag-orch")  # first drain: surfaces the conflict
    await h.drain(orch_id="ag-orch")  # second drain: MUST be skipped

    async with SessionLocal() as db:
        rows = await storage_repo.list_conflicts(db, conv_id)
        msgs, _ = await storage_repo.list_messages(db, conv_id, limit=50)
    conflict_cards = [m for m in msgs if m["payload"].get("kind") == "conflict"]

    assert len(rows) == 1, f"duplicate conflict rows: {[r.branch for r in rows]}"
    assert len(conflict_cards) == 1, "duplicate conflict card in timeline"
    assert len(h.cards_emitted) == 1, "conflict card broadcast more than once"
    assert len(h.spawns) == 1, f"auto-fix spawned {len(h.spawns)}x (double-spawn)"


# ── (2) failed auto-fix reverts conflict to 'open' → re-attempt AT MOST once ──
@pytest.mark.asyncio
async def test_failed_autofix_reopen_respawns_at_most_once(env):
    """Scenario (2): an auto-fix attempt fails and the conflict reverts to
    'open' (the row is NOT abandoned). 'open' is in the skip set, so as long as
    the open row exists the branch stays skipped — the next drain must NOT mint
    a second row nor fire a second spawn. (If a human/agent later abandons it,
    'abandoned' is ALSO skipped, so still no respawn.) Guards against the
    infinite-respawn loop on a perpetually-failing fix."""
    conv_id, ws_id = new_ulid(), "wsREOPEN"
    await _seed_conv(conv_id, group=True, orch="ag-orch")
    _branch, _files, _root = await _build_content_conflict(ws_id, conv_id)

    h = _DrainHarness(conv_id, ws_id)
    await h.drain(orch_id="ag-orch")
    assert len(h.spawns) == 1  # initial auto-fix spawn
    async with SessionLocal() as db:
        rows = await storage_repo.list_conflicts(db, conv_id)
        assert len(rows) == 1
        cid = rows[0].id
        assert rows[0].status == "open"  # a fresh conflict starts open

    # Simulate a FAILED auto-fix: status flips resolving → back to open (the row
    # is never abandoned; the branch still conflicts on disk — nothing merged).
    async with SessionLocal() as db:
        await storage_repo.set_conflict_status(db, cid, "resolving")
        await db.commit()
    # 'resolving' is in the skip set → drain is a no-op (fix turn is in flight).
    await h.drain(orch_id="ag-orch")
    assert len(h.spawns) == 1, "spawned again while fix was still 'resolving'"

    async with SessionLocal() as db:
        await storage_repo.set_conflict_status(db, cid, "open")  # fix failed → reopen
        await db.commit()
    # 'open' is in the skip set → STILL a no-op. No infinite respawn.
    await h.drain(orch_id="ag-orch")

    async with SessionLocal() as db:
        rows = await storage_repo.list_conflicts(db, conv_id)
    assert len(rows) == 1, f"reopen minted a duplicate row: {len(rows)}"
    assert len(h.spawns) == 1, (
        f"failed-fix reopen respawned the auto-fix ({len(h.spawns)} total) — "
        "infinite-respawn risk"
    )


# ── (3) resolver selection: DM/solo→branch author; group→orchestrator ──
@pytest.mark.asyncio
async def test_resolver_is_branch_author_in_dm(env):
    """Scenario (3a): SOLO/DM conv (no orchestrator). The conflict must be
    resolved by the BRANCH AUTHOR (only party present — no judge-and-party
    bias). The spawned resolver must be ag-D, never 'orchestrator'."""
    conv_id, ws_id = new_ulid(), "wsDM"
    await _seed_conv(conv_id, group=False, orch=None)
    _branch, _files, _root = await _build_content_conflict(ws_id, conv_id)

    h = _DrainHarness(conv_id, ws_id)
    # In a DM drain the speaking agent is passed as orch_id; it is NOT the
    # conv's orchestrator (there is none) so the author branch must fire.
    await h.drain(orch_id="ag-D")

    assert len(h.spawns) == 1
    resolver, _b = h.spawns[0]
    assert resolver == "ag-D", f"DM resolver should be branch author, got {resolver!r}"


@pytest.mark.asyncio
async def test_resolver_is_orchestrator_in_group(env):
    """Scenario (3b): GROUP conv (has orchestrator). The neutral ORCHESTRATOR
    resolves — never the branch author (avoids judge-and-party). Fires only when
    orch_id is the conv's TRUE orchestrator (the burst case)."""
    conv_id, ws_id = new_ulid(), "wsGRP"
    await _seed_conv(conv_id, group=True, orch="ag-orch")
    _branch, _files, _root = await _build_content_conflict(ws_id, conv_id)

    h = _DrainHarness(conv_id, ws_id)
    await h.drain(orch_id="ag-orch")  # the true orchestrator drains the burst

    assert len(h.spawns) == 1
    resolver, _b = h.spawns[0]
    assert resolver == "ag-orch", f"group resolver should be orchestrator, got {resolver!r}"
    assert resolver != "ag-D", "group must NOT spawn the branch author (judge-and-party)"


@pytest.mark.asyncio
async def test_group_non_orchestrator_drain_does_not_spawn(env):
    """Scenario (3c) — edge: a non-burst GROUP drain where the speaking agent
    (orch_id) is NOT the conv orchestrator. The drain must NOT spawn the author
    (group has an orchestrator → author-resolve is suppressed) and must NOT spawn
    the speaker (not the true orchestrator). Routing to the orchestrator is left
    to _maybe_handoff_to_orchestrator. So: a row+card is surfaced, but ZERO
    auto-fix spawn from the drain itself."""
    conv_id, ws_id = new_ulid(), "wsGRPNB"
    await _seed_conv(conv_id, group=True, orch="ag-orch")
    _branch, _files, _root = await _build_content_conflict(ws_id, conv_id)

    h = _DrainHarness(conv_id, ws_id)
    # ag-D (a worker) speaks; it is NOT the conv's orchestrator.
    await h.drain(orch_id="ag-D")

    async with SessionLocal() as db:
        rows = await storage_repo.list_conflicts(db, conv_id)
    assert len(rows) == 1, "conflict still surfaced exactly once"
    assert h.spawns == [], (
        f"non-orchestrator group drain must not auto-spawn; got {h.spawns}"
    )


# ── (4) manual mode: conflict surfaced, but NO spawn (left for the user) ──
@pytest.mark.asyncio
async def test_manual_mode_surfaces_conflict_without_spawn(env):
    """merge_mode='manual' → the conflict card is created for the user to decide,
    but no auto-fix turn is spawned. Confirms the spawn is gated on auto mode and
    the row is still surfaced exactly once across two drains (skip still holds)."""
    conv_id, ws_id = new_ulid(), "wsMANUAL"
    await _seed_conv(conv_id, group=True, orch="ag-orch", merge_mode="manual")
    _branch, _files, _root = await _build_content_conflict(ws_id, conv_id)

    h = _DrainHarness(conv_id, ws_id)
    await h.drain(orch_id="ag-orch")
    await h.drain(orch_id="ag-orch")

    async with SessionLocal() as db:
        rows = await storage_repo.list_conflicts(db, conv_id)
    assert len(rows) == 1, "manual mode duplicated the conflict row across drains"
    assert h.spawns == [], "manual mode must not auto-spawn a fix turn"
