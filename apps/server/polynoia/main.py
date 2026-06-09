"""Polynoia FastAPI app entry."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from polynoia.api.contacts_routes import router as contacts_router
from polynoia.api.conversations_routes import router as conversations_router
from polynoia.api.ws_conv import ws_router
from polynoia.api.onboarding import router as onboarding_router
from polynoia.api.routes import router
from polynoia.api.terminal import router as terminal_router
from polynoia.api.workspace_files import router as workspace_files_router
from polynoia.api.workspaces_routes import router as workspaces_router
from polynoia.settings import settings
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import SessionLocal, dispose_engine
from polynoia.storage.repo import reap_orphan_tool_calls

# App-level logging. uvicorn configures only its own loggers and leaves the
# root handler-less, so our `logging.getLogger("polynoia.*")` calls would be
# swallowed. Install a root StreamHandler at INFO so app logs (orchestration
# lifecycle, etc.) actually surface in the server output.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup: ensure DB schema + seed-if-empty
    await bootstrap_db()
    # Reap any tool-call payloads left at running/pending from a previous
    # process that died mid-turn (uvicorn --reload, kill, OOM …). Without
    # this, the UI's 进行中 spinner sticks forever on those tool cards.
    async with SessionLocal() as _s:
        _reaped = await reap_orphan_tool_calls(_s)
    if _reaped:
        logging.getLogger("polynoia.main").info(
            "reaped %d orphan tool-call(s) left at running/pending",
            _reaped,
        )
    # Hydrate custom-workspace locations into the sandbox resolver so agents on
    # any conv resolve the right real dir + integration branch after a restart
    # (the sandbox layer is storage-agnostic; only routes/main touch the DB).
    import json as _json
    from pathlib import Path as _Path

    from polynoia.sandbox import register_workspace_location
    from polynoia.storage import repo as _repo

    def _resolve_branch(path: str, db_value: str | None) -> str | None:
        # Prefer the DB value; else recover from the manifest adopt/init wrote
        # (so a row with path but NULL integration_branch doesn't silently fall
        # back to "main" for a repo whose real branch is master/develop/…).
        if db_value:
            return db_value
        mani = _Path(path) / ".polynoia" / "manifest.json"
        if mani.exists():
            try:
                return _json.loads(mani.read_text()).get("integration_branch") or None
            except Exception:  # noqa: BLE001
                return None
        return None

    async with SessionLocal() as _s:
        _dirty = False
        for _w in await _repo.list_workspaces(_s):
            if not getattr(_w, "path", None):
                continue
            _branch = _resolve_branch(_w.path, _w.integration_branch)
            register_workspace_location(_w.id, path=_w.path, integration_branch=_branch)
            if _branch and _branch != _w.integration_branch:
                _w.integration_branch = _branch  # persist the recovered value
                await _repo.upsert_workspace(_s, _w)
                _dirty = True
        if _dirty:
            await _s.commit()
    yield
    # Shutdown
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Polynoia",
        description="IM-style multi-agent collaboration platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        # Local/mobile dev server: allow any browser/WebView origin. The app
        # does not use cookie auth, so we can keep credentials disabled and
        # return the standards-compliant wildcard header.
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(onboarding_router)
    app.include_router(terminal_router)
    app.include_router(workspace_files_router)
    app.include_router(workspaces_router)
    app.include_router(contacts_router)
    app.include_router(conversations_router)
    app.include_router(ws_router)
    return app


app = create_app()
