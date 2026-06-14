"""Polynoia multi-agent collaboration platform — backend."""

__version__ = "0.1.0"


def _suppress_windows_subprocess_consoles() -> None:
    """Stop child processes from flashing a console window on Windows.

    When the desktop app (a GUI process with no console) spawns the backend, and
    the backend in turn spawns console programs — `uv`, the adapter CLIs
    (`claude` / `codex` / `opencode`), the `python -m polynoia.mcp` server, git,
    etc. — Windows allocates a brand-new console window for each one. Those
    windows are not just ugly: closing one kills that process (the user closing
    the backend's window dropped the whole connection).

    There is no per-process global flag, so we patch the single chokepoint every
    spawn funnels through — ``subprocess.Popen`` (used by ``asyncio``'s subprocess
    transport AND by anyio, which the Claude Agent SDK uses) — to default
    ``creationflags`` to ``CREATE_NO_WINDOW``. Callers that set their own flags are
    left untouched. No-op off Windows.
    """
    import sys

    if sys.platform != "win32":
        return
    import subprocess

    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x0800_0000)
    if getattr(subprocess.Popen, "_polynoia_no_window", False):
        return  # idempotent — a re-import must not double-wrap

    _orig_init = subprocess.Popen.__init__

    def _init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = create_no_window
        return _orig_init(self, *args, **kwargs)

    _init._polynoia_no_window = True  # type: ignore[attr-defined]
    subprocess.Popen.__init__ = _init  # type: ignore[method-assign]
    subprocess.Popen._polynoia_no_window = True  # type: ignore[attr-defined]


_suppress_windows_subprocess_consoles()
