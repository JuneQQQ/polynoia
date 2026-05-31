"""Polynoia server settings (env-driven)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLYNOIA_", env_file=".env", extra="ignore")

    # Server
    host: str = "0.0.0.0"
    port: int = 7780
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Storage
    db_url: str = "sqlite+aiosqlite:///./polynoia.db"
    sandbox_root: Path = Path.home() / "sandbox" / "polynoia"

    # Project runner — Docker-isolated whole-project live preview (ADR-018).
    # Collapses "host needs Node/Python/<versions>" into "host needs Docker"
    # + reproducible env + real isolation (mem/cpu/pids). Host ports are bound
    # to 127.0.0.1 only; the iframe connects directly to localhost:{host_port}.
    runner_node_image: str = "node:20-slim"
    runner_python_image: str = "python:3.12-slim"
    runner_port_base: int = 7800
    runner_port_span: int = 100
    runner_memory: str = "512m"
    runner_cpus: str = "1"

    # Anthropic / OpenAI
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None


settings = Settings()
