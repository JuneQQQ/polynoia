"""ADVERSARIAL security tests — path traversal, upload limits, file-access guards.

Angle: confine every file operation to its sandbox root, reject traversal in
every form (``..`` segments, leading ``/``, symlink escape, NUL byte, percent-
encoded), and enforce the 25MB upload ceiling at the boundary.

Everything runs against ISOLATED tmp roots (monkeypatched ``settings.sandbox_root``
and ``settings.files_dir``) plus FastAPI ``HTTPException`` assertions on the pure
guard helpers. NO live :7780, NO ``~/.polynoia``, NO network, NO LLM.

If a traversal SUCCEEDS (resolves/writes/reads outside the sandbox root) that is a
CRITICAL bug and the assertion is kept FAILING on purpose — never weaken it.

Targets:
  - polynoia.api._fs_paths._resolve_safe_path  (the traversal guard)
  - polynoia.api._fs_paths._workspace_root      (conv: address resolution)
  - polynoia.api.routes._safe_conv_token / _safe_upload_name / serve_conv_upload
  - polynoia.api.routes.upload_file             (25MB boundary, per-conv landing)
  - polynoia.api.routes.present_file            (path quoting in the file src URL)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import polynoia.storage.db as db_module
from polynoia.api import routes
from polynoia.api._fs_paths import _resolve_present_path, _resolve_safe_path, _workspace_root
from polynoia.domain.entities import Conversation, new_ulid
from polynoia.storage import repo as storage_repo


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — isolated tmp sandbox, never touches the real ~/sandbox or ~/.polynoia
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    """A bare workspace root with a single in-bounds file. The PARENT holds a
    'secret' file that any successful escape would expose — so an escape is
    observable, not just theoretically out-of-tree."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "inside.txt").write_text("in-bounds", encoding="utf-8")
    # A juicy target one level up — reading this == confinement breach.
    (tmp_path / "secret.txt").write_text("TOP SECRET — must never be served", encoding="utf-8")
    return root


@pytest.fixture
async def route_db(monkeypatch, tmp_path: Path):
    """Isolated tmp SQLite — mirrors tests/api/test_present_policy.py:route_db so
    present_file runs end-to-end against a throwaway DB, never the live store."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/security-paths.db"
    engine = create_async_engine(
        db_url, echo=False, future=True,
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


@pytest.fixture
def sandbox_env(monkeypatch, tmp_path: Path) -> Path:
    """Point settings.sandbox_root + files_dir at isolated tmp dirs so upload /
    serve endpoints can't touch the host. Returns the sandbox root."""
    from polynoia.settings import settings

    sbx = tmp_path / "sandbox"
    sbx.mkdir()
    files = tmp_path / "files"
    files.mkdir()
    monkeypatch.setattr(settings, "sandbox_root", sbx)
    monkeypatch.setattr(settings, "files_dir", files)
    return sbx


def _escaped(resolved: Path, root: Path) -> bool:
    """True iff ``resolved`` is NOT confined under ``root`` (a confinement breach)."""
    try:
        resolved.resolve().relative_to(root.resolve())
        return False
    except ValueError:
        return True


# ─────────────────────────────────────────────────────────────────────────────
# (1) _resolve_safe_path — read/write traversal guard
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "evil",
    [
        "../../etc/passwd",
        "../secret.txt",
        "../../secret.txt",
        "a/b/../../../secret.txt",
        "./../../secret.txt",
        "subdir/../../secret.txt",
    ],
)
def test_dotdot_escape_is_rejected(ws_root: Path, evil: str) -> None:
    """Any ``..`` chain that climbs above the workspace root → 400, never a path
    that reads the parent's secret.txt."""
    with pytest.raises(HTTPException) as exc:
        _resolve_safe_path(ws_root, evil)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "absolute",
    ["/etc/passwd", "/secret.txt", "//etc/passwd", "/"],
)
def test_absolute_path_is_rejected(ws_root: Path, absolute: str) -> None:
    """A leading ``/`` makes ``workspace_root / rel`` discard the root entirely
    (pathlib semantics) — must be caught BEFORE the join, as a 400."""
    with pytest.raises(HTTPException) as exc:
        _resolve_safe_path(ws_root, absolute)
    assert exc.value.status_code == 400
    # Belt-and-suspenders: prove the join would have escaped if not rejected.
    assert _escaped(ws_root / absolute, ws_root) or absolute == "/"


def test_symlink_file_escape_is_rejected(ws_root: Path) -> None:
    """A symlink INSIDE the workspace pointing at the parent's secret must not let
    a read follow it out — resolve() dereferences, the guard must still reject."""
    link = ws_root / "escape_link"
    os.symlink(ws_root.parent / "secret.txt", link)
    with pytest.raises(HTTPException) as exc:
        _resolve_safe_path(ws_root, "escape_link")
    assert exc.value.status_code == 400


def test_symlink_dir_escape_is_rejected(ws_root: Path) -> None:
    """A symlinked DIR pointing at the parent + a child path traverses through it.
    resolve() collapses the symlink, so the resolved target is outside root → 400.
    This is the classic 'symlink then walk' escape."""
    os.symlink(ws_root.parent, ws_root / "up")
    with pytest.raises(HTTPException) as exc:
        _resolve_safe_path(ws_root, "up/secret.txt")
    assert exc.value.status_code == 400


def test_nul_byte_in_path_is_rejected_cleanly(ws_root: Path) -> None:
    """A NUL byte in the path must be REJECTED as a 400 (bad input), NOT crash the
    handler with an unhandled ValueError (which surfaces as a 500).

    BUG: _resolve_safe_path calls ``(workspace_root / rel_path).resolve()`` without
    guarding the embedded-NUL ValueError that pathlib's lstat raises. So a crafted
    ``foo\\x00bar`` path escapes the try/except (which only catches the
    relative_to ValueError) and bubbles a raw ValueError out of the endpoint ->
    HTTP 500 instead of a clean 400. A NUL byte is never a legitimate path
    component; it should be classified as a rejected traversal/bad-path input.

    Kept FAILING to document the defect — do not weaken to expect ValueError."""
    with pytest.raises(HTTPException) as exc:
        _resolve_safe_path(ws_root, "a\x00/../../secret.txt")
    assert exc.value.status_code == 400


def test_inbounds_dotdot_still_resolves_within(ws_root: Path) -> None:
    """Sanity counter-case: ``a/../inside.txt`` stays inside, so it must NOT be
    rejected (guard precise, not a blanket '..' string ban)."""
    resolved = _resolve_safe_path(ws_root, "a/../inside.txt")
    assert resolved == ws_root / "inside.txt"
    assert not _escaped(resolved, ws_root)


# ─────────────────────────────────────────────────────────────────────────────
# (1b) _resolve_present_path — the read/preview/download entry; same guard plus a
#      worktree fallback that must NOT widen the traversal surface.
# ─────────────────────────────────────────────────────────────────────────────
def _bootstrap_ws(sandbox_root: Path, ws_id: str) -> Path:
    """Make ``<sandbox>/workspaces/<ws_id>`` look bootstrapped (has .git) so
    _workspace_root accepts it without spawning real git."""
    root = sandbox_root / "workspaces" / ws_id
    (root / ".git").mkdir(parents=True)
    return root


def test_present_path_rejects_traversal(sandbox_env: Path) -> None:
    """_resolve_present_path runs _resolve_safe_path first; a ``..`` escape must
    raise 400 before any worktree fallback can serve a host file."""
    _bootstrap_ws(sandbox_env, "ws1")
    (sandbox_env / "host_secret.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        _resolve_present_path("ws1", "../../host_secret.txt")
    assert exc.value.status_code == 400


def test_present_path_worktree_fallback_stays_in_tree(sandbox_env: Path) -> None:
    """The worktree fallback joins ``wt / rel_path``; with a clean rel_path it can
    only reach files under a worktree dir, never the host. Verify a normal miss
    returns an in-tree (non-existent) path, and that a traversal rel_path is
    already rejected upstream (covered above) so it never reaches the fallback."""
    root = _bootstrap_ws(sandbox_env, "ws2")
    wt = root / "worktrees" / "agent-a"
    wt.mkdir(parents=True)
    (wt / "made.txt").write_text("from worktree", encoding="utf-8")
    # main misses, worktree has it -> served from worktree, still under root.
    resolved = _resolve_present_path("ws2", "made.txt")
    assert resolved == wt / "made.txt"
    assert not _escaped(resolved, root)


# ─────────────────────────────────────────────────────────────────────────────
# (1c) _workspace_root — conv: addressing. The conv id is concatenated onto
#      sandbox_root WITHOUT a confinement check; only a stray .git gates the
#      escape. Document the confinement gap.
# ─────────────────────────────────────────────────────────────────────────────
def test_conv_address_traversal_is_confined_to_sandbox(sandbox_env: Path) -> None:
    """A ``conv:`` workspace id is ``settings.sandbox_root / id``. A traversal id
    like ``conv:../../../escaped`` must NOT resolve to a root OUTSIDE the sandbox.

    WEAKNESS: _workspace_root does ``(settings.sandbox_root / ws_id[5:]).resolve()``
    with NO relative_to(sandbox_root) confinement check — unlike _resolve_safe_path.
    The ONLY thing standing between a crafted conv: id and an arbitrary host
    directory is the ``(root / '.git').exists()`` 404 gate. So if any git repo
    exists at the climbed-to location, that host tree becomes a browsable
    workspace root and _resolve_safe_path then happily confines *within the
    escaped root*. Here we plant a .git outside the sandbox and assert the conv:
    id is NOT allowed to anchor there. Kept FAILING to document the missing
    confinement; do not weaken."""
    # An attacker-influenced sibling dir outside the sandbox, made to look like a repo.
    outside = sandbox_env.parent / "outside_repo"
    (outside / ".git").mkdir(parents=True)
    (outside / "loot.txt").write_text("host file", encoding="utf-8")

    # Climb out of <sandbox> into the sibling 'outside_repo'.
    rel_up = os.path.relpath(outside, sandbox_env)  # e.g. '../outside_repo'
    evil_id = f"conv:{rel_up}"

    try:
        root = _workspace_root(evil_id)
    except HTTPException as exc:
        # A 400/404 that refuses the escape is the SECURE outcome.
        assert exc.status_code in (400, 404)
        return
    # If it resolved, the root MUST still be confined under the sandbox.
    assert not _escaped(root, sandbox_env), (
        f"conv: traversal escaped sandbox: {root} is outside {sandbox_env}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# (2) /api/files/raw guard — serve_conv_upload(conv, name)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "bad_conv",
    ["../../etc", "..", "/etc", "dm-../x", "dm-", "a/b", "a.b", "conv with space", ""],
)
def test_files_raw_rejects_bad_conv_token(bad_conv: str) -> None:
    """_safe_conv_token must reject anything that isn't a bare ULID or dm-<ULID>;
    a separator/.. would let _conv_upload_dir anchor outside the sandbox."""
    assert routes._safe_conv_token(bad_conv) is False


@pytest.mark.parametrize("good_conv", ["01ABCxyz0123", "dm-01ABCxyz0123", "abc123"])
def test_files_raw_accepts_clean_conv_token(good_conv: str) -> None:
    assert routes._safe_conv_token(good_conv) is True


@pytest.mark.asyncio
async def test_serve_conv_upload_rejects_traversal_conv(sandbox_env: Path) -> None:
    """A traversal-laden ``conv`` is bounced with 400 before any disk access."""
    with pytest.raises(HTTPException) as exc:
        await routes.serve_conv_upload("../../../../etc", "passwd")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_serve_conv_upload_name_traversal_cannot_read_parent(
    sandbox_env: Path, monkeypatch
) -> None:
    """Even with a VALID conv token, a ``name`` carrying ``../`` must be flattened
    by os.path.basename so the read can't climb out of the conv uploads dir.

    We plant a secret one level ABOVE uploads/ and ask for it via '../secret'.
    basename('../secret') == 'secret' -> looks for uploads/secret (404), never the
    parent's file. A 404 (not the secret bytes) is the pass condition."""
    conv = "01CONVabc123"
    updir = sandbox_env / conv / "uploads"
    updir.mkdir(parents=True)
    # secret sits in the conv root, just above uploads/
    (sandbox_env / conv / "secret").write_text("PARENT SECRET", encoding="utf-8")

    # _conv_upload_dir hits the DB for workspace_id; stub it to our isolated dir.
    async def _fake_dir(cid, *, create=True):
        assert cid == conv
        return updir

    monkeypatch.setattr(routes, "_conv_upload_dir", _fake_dir)

    with pytest.raises(HTTPException) as exc:
        await routes.serve_conv_upload(conv, "../secret")
    assert exc.value.status_code == 404  # flattened to uploads/secret, which doesn't exist


# ─────────────────────────────────────────────────────────────────────────────
# (3) /api/upload — 25MB boundary + filename can't write outside uploads/
# ─────────────────────────────────────────────────────────────────────────────
class _FakeRequest:
    """Minimal stand-in for fastapi.Request: bytes body + a content-type header."""

    def __init__(self, body: bytes, content_type: str = "application/octet-stream"):
        self._body = body
        self.headers = {"content-type": content_type}

    async def body(self) -> bytes:
        return self._body


def _patch_upload_dir(monkeypatch, updir: Path) -> None:
    async def _fake_dir(conv_id, *, create=True):
        if create:
            updir.mkdir(parents=True, exist_ok=True)
        return updir

    monkeypatch.setattr(routes, "_conv_upload_dir", _fake_dir)


@pytest.mark.asyncio
async def test_upload_at_25mb_boundary_is_accepted(sandbox_env: Path, monkeypatch) -> None:
    """Exactly 25*1024*1024 bytes is the limit; the check is ``> MAX`` so the
    boundary value must be ACCEPTED, not 413'd off-by-one."""
    updir = sandbox_env / "convX" / "uploads"
    _patch_upload_dir(monkeypatch, updir)
    body = b"x" * routes.MAX_UPLOAD_BYTES  # exactly 25MB
    req = _FakeRequest(body)
    res = await routes.upload_file(req, name="big.bin", conv_id="convX")
    assert res["size_bytes"] == routes.MAX_UPLOAD_BYTES
    assert (updir / "big.bin").read_bytes() == body


@pytest.mark.asyncio
async def test_upload_over_25mb_is_413(sandbox_env: Path, monkeypatch) -> None:
    """One byte over the ceiling (and a 26MB body) must be rejected with 413, and
    nothing written to disk."""
    updir = sandbox_env / "convX" / "uploads"
    _patch_upload_dir(monkeypatch, updir)
    body = b"x" * (routes.MAX_UPLOAD_BYTES + 1)
    req = _FakeRequest(body)
    with pytest.raises(HTTPException) as exc:
        await routes.upload_file(req, name="big.bin", conv_id="convX")
    assert exc.value.status_code == 413
    assert not (updir / "big.bin").exists()


@pytest.mark.asyncio
async def test_upload_filename_traversal_stays_in_uploads(sandbox_env: Path, monkeypatch) -> None:
    """A path-laden ``name`` (``../../escape.sh``, ``/etc/cron.d/x``) must be
    flattened to a basename and land INSIDE uploads/ — never write to the parent
    or an absolute host path."""
    updir = sandbox_env / "convX" / "uploads"
    _patch_upload_dir(monkeypatch, updir)

    for evil_name in ["../../escape.sh", "/etc/cron.d/pwn", "..\\..\\win.bat", "sub/dir/x.txt"]:
        req = _FakeRequest(b"payload")
        res = await routes.upload_file(req, name=evil_name, conv_id="convX")
        written = Path(res["path"])
        # The written file must be a direct child of uploads/ — no climb, no abs.
        assert written.parent.resolve() == updir.resolve(), (
            f"{evil_name!r} escaped uploads/: wrote to {written}"
        )
        assert not _escaped(written, updir)
        # And nothing got written above uploads/.
        assert not (sandbox_env / "convX" / "escape.sh").exists()
        assert not Path("/etc/cron.d/pwn").exists()


@pytest.mark.asyncio
async def test_empty_upload_is_400(sandbox_env: Path, monkeypatch) -> None:
    _patch_upload_dir(monkeypatch, sandbox_env / "convX" / "uploads")
    with pytest.raises(HTTPException) as exc:
        await routes.upload_file(_FakeRequest(b""), name="x", conv_id="convX")
    assert exc.value.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# (4) present_file — the file `src` URL must percent-encode the path so a path
#     with `..`/`/`/`?`/`&`/`#` can't reshape the download URL or smuggle query
#     params. (present itself only records a URL; the download endpoint re-guards,
#     but the URL must be well-formed and the raw `..` must not pass through.)
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_present_file_quotes_path_in_src_url(route_db, monkeypatch) -> None:
    """The file src is built as
    ``/api/workspaces/{ws}/files/download?path=`` + quote(path, safe="").

    A traversal/odd path must be FULLY percent-encoded: no raw ``/`` or ``..``
    sequence and no stray ``&``/``#`` that could append extra query params or a
    fragment to the download URL. Runs against the isolated tmp DB; only the WS
    broadcast is stubbed (no real socket)."""
    session_maker = route_db
    conv_id = new_ulid()
    agent = "agent-direct"
    async with session_maker() as db:
        await storage_repo.create_conversation(
            db,
            Conversation(
                id=conv_id, title="dm", members=["you", agent], direct=True, group=False
            ),
        )
        await db.commit()

    async def _noop_broadcast(*a, **k):
        return None

    monkeypatch.setattr(routes, "_broadcast_to_conv", _noop_broadcast)

    evil_path = "../../etc/passwd?x=1&y=2#frag"
    res = await routes.present_file(
        {
            "conv_id": conv_id,
            "agent_id": agent,
            "ws": "ws-1",
            "path": evil_path,
            "message": "here",
        }
    )
    assert res["ok"] is True

    async with session_maker() as db:
        msgs, _ = await storage_repo.list_messages(db, conv_id, limit=20)
    file_msgs = [m for m in msgs if m["payload"].get("kind") == "files"]
    assert len(file_msgs) == 1
    files = file_msgs[0]["payload"]["files"]
    assert len(files) == 1
    src = files[0]["src"]
    # The query string after path= must be a single, fully-encoded token: no raw
    # slash, no raw '..' boundary that re-parses, no stray '&'/'#'.
    assert src.startswith("/api/workspaces/ws-1/files/download?path=")
    encoded = src.split("path=", 1)[1]
    assert "/" not in encoded, f"raw slash leaked into src URL: {src}"
    assert "?" not in encoded and "#" not in encoded, f"fragment/query leaked: {src}"
    assert "&" not in encoded, f"extra query param could be smuggled: {src}"
    # The leading-slash strip means the stored name is still the basename only.
    assert files[0]["name"] == "passwd?x=1&y=2#frag".split("/")[-1]
