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

    # Storage — strict platform/user data separation:
    #   • PLATFORM data (providers/adapters/agents/servers/workspace metadata/
    #     conversations/messages/pins/orchestration state) → the central SQLite
    #     DB below, one per Polynoia instance/account, under ~/.polynoia/.
    #   • USER data (agent-written code, git history, artifacts) → the filesystem
    #     git worktrees under `sandbox_root` or the user's real `Workspace.path`.
    #   • BLOBs (chat attachments) → `files_dir`; payloads store a short URL only.
    # `~/.polynoia/` keeps platform data in a stable per-user home, not buried in
    # the repo's cwd. Override any of these via POLYNOIA_DB_URL / _FILES_DIR /
    # _SANDBOX_ROOT.
    polynoia_home: Path = Path.home() / ".polynoia"
    db_url: str = f"sqlite+aiosqlite:///{Path.home() / '.polynoia' / 'polynoia.db'}"
    files_dir: Path = Path.home() / ".polynoia" / "files"
    sandbox_root: Path = Path.home() / "sandbox" / "polynoia"

    # Anthropic / OpenAI
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None


settings = Settings()
