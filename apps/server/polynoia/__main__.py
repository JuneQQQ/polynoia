"""Server launcher — ``python -m polynoia`` (e.g. ``uv run python -m polynoia``).

Why not just call the ``uvicorn`` CLI? Because on Windows we must override
uvicorn's loop factory so it can spawn subprocesses (adapter CLIs, onboarding
probe) while keeping ``--reload``. The CLI offers no hook for that; a
programmatic launch does. See ``polynoia/_winloop.py`` for the full rationale.

Config via env (so ``dev.ps1`` / Makefile can stay declarative):
  POLYNOIA_HOST   default 0.0.0.0
  POLYNOIA_PORT   default 7780
  POLYNOIA_RELOAD default 1 (set 0/false/no to disable hot-reload)

On non-Windows this behaves exactly like ``uvicorn polynoia.main:app --reload``.
"""

from __future__ import annotations

import os
import sys


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def main() -> None:
    import uvicorn

    host = os.environ.get("POLYNOIA_HOST", "0.0.0.0")
    port = int(os.environ.get("POLYNOIA_PORT", "7780"))
    reload = _env_flag("POLYNOIA_RELOAD", default=True)

    config = uvicorn.Config("polynoia.main:app", host=host, port=port, reload=reload)

    # Windows: force a subprocess-capable ProactorEventLoop (see _winloop). The
    # override rides on the pickled config into reload worker children.
    if sys.platform == "win32":
        from polynoia._winloop import proactor_loop_factory

        config.get_loop_factory = proactor_loop_factory  # type: ignore[method-assign]

    server = uvicorn.Server(config)
    if config.should_reload:
        # Mirror uvicorn.main.run()'s reload branch so our overridden config is
        # the one the reloader pickles to its spawned worker.
        from uvicorn.supervisors import ChangeReload

        sock = config.bind_socket()
        ChangeReload(config, target=server.run, sockets=[sock]).run()
    else:
        server.run()


if __name__ == "__main__":
    main()
