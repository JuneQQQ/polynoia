"""Polynoia FastAPI app entry."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from polynoia.api.onboarding import router as onboarding_router
from polynoia.api.routes import router
from polynoia.api.terminal import router as terminal_router
from polynoia.settings import settings
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import dispose_engine

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
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(onboarding_router)
    app.include_router(terminal_router)
    return app


app = create_app()
