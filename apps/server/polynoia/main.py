"""Polynoia FastAPI app entry."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from polynoia.api.onboarding import router as onboarding_router
from polynoia.api.routes import router
from polynoia.settings import settings
from polynoia.storage.bootstrap import bootstrap_db
from polynoia.storage.db import dispose_engine


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
    return app


app = create_app()
