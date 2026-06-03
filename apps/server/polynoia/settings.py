"""Polynoia server settings (env-driven)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLYNOIA_", env_file=".env", extra="ignore")

    # Server
    host: str = "0.0.0.0"
    port: int = 7780
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        # Packaged desktop builds (Tauri 2) load from a custom-scheme origin and
        # call the server cross-origin; without these the .app/.dmg is CORS-blocked.
        "tauri://localhost",       # macOS / Linux
        "http://tauri.localhost",  # Windows
    ]

    # Storage
    db_url: str = "sqlite+aiosqlite:///./polynoia.db"
    sandbox_root: Path = Path.home() / "sandbox" / "polynoia"

    # Anthropic / OpenAI
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None


settings = Settings()
