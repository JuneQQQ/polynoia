"""Polynoia server settings (env-driven)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLYNOIA_", env_file=".env", extra="ignore")

    # Server
    host: str = "0.0.0.0"
    port: int = 7780
    cors_origins: list[str] = [
        "http://localhost:7788",
        "http://127.0.0.1:7788",
        # Packaged desktop builds (Tauri 2) load from a custom-scheme origin and
        # call the server cross-origin; without these the .app/.dmg is CORS-blocked.
        "tauri://localhost",  # macOS / Linux
        "http://tauri.localhost",  # Windows
        # Mobile (Capacitor) WebView origins — the app loads from a localhost
        # scheme and calls the remote server cross-origin. The exact string
        # depends on the Capacitor scheme (https with iosScheme/androidScheme
        # "https"; capacitor:// legacy), so list all; confirm from server logs on
        # first device run. Overridable via POLYNOIA_CORS_ORIGINS.
        "https://localhost",  # Capacitor iOS/Android (scheme "https")
        "capacitor://localhost",  # Capacitor legacy/native scheme
        "http://localhost",  # defensive
        # LAN dev: an Android WebView / phone loads the Vite dev page from the
        # host's LAN IP, then calls the API cross-origin. Both the page origin
        # (:7788) and a direct-backend origin (:7780) must be allowed, else the
        # browser CORS-blocks every request and the app shows no data. Change the
        # IP to your host, or set POLYNOIA_CORS_ORIGINS to override the whole list.
        "http://10.12.48.166:7788",
        "http://10.12.48.166:7780",
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
    # Installed skill packages (a folder per skill: SKILL.md + resources/scripts),
    # platform-level + reusable across contacts. Fetched from a git URL / local
    # path into here, then placed into each agent's sandbox skills dir at spawn.
    skills_dir: Path = Path.home() / ".polynoia" / "skills"
    # Proxy for `git clone` when installing a skill from a URL. None → git
    # inherits the backend process's http(s)_proxy env (how it's launched behind
    # the GFW). Set POLYNOIA_GIT_PROXY to override explicitly (e.g.
    # http://127.0.0.1:7890).
    git_proxy: str | None = None
    db_url: str = f"sqlite+aiosqlite:///{Path.home() / '.polynoia' / 'polynoia.db'}"
    files_dir: Path = Path.home() / ".polynoia" / "files"
    sandbox_root: Path = Path.home() / "sandbox" / "polynoia"

    # Anthropic / OpenAI
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None


settings = Settings()
