"""Interactive terminal — a real PTY bridged to the browser over WebSocket.

A user opens a terminal tab in the center column (CenterTabs) → the frontend
(xterm.js) connects here → we spawn an interactive shell inside a pseudo-tty
whose ``cwd`` is the workspace's **main** working directory
(``<sandbox_root>/workspaces/<ws_id>``). Keystrokes flow browser → master fd;
shell output flows master fd → browser. This is the workspace's real `main`
checkout, so `git status` / `ls` / `python` behave exactly as on disk.

Wire protocol (per WebSocket frame):
  - **binary** frame  = raw pty input (the bytes xterm produced for keystrokes)
  - **text** frame    = a JSON control message, currently only
                        ``{"type": "resize", "cols": N, "rows": M}``

Security (P0): single-machine dev tool. This is a real shell with the host
environment, scoped only by its starting directory — matching the plan's
"真·交互式终端(单机工具,可接受)". The sole gate is that the conversation's
workspace is a real one (validated against the DB); its main dir is bootstrapped
on demand if no agent has run there yet. No CPU/RAM isolation in P0 (see CLAUDE.md
§6.2); P1 adds nsjail/Docker.
"""

import asyncio
import contextlib
import json
import logging
import os
import struct

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from polynoia.sandbox import Sandbox
from polynoia.storage.db import SessionLocal
from polynoia.storage.models import WorkspaceRow

# The interactive terminal is a real Unix PTY (fcntl/pty/termios/signal). Those
# modules don't exist on Windows, so importing them at top level would crash the
# whole server on import. Guard them: on a non-POSIX host the server still starts
# fine and only this one WebSocket endpoint degrades (closes with a clear reason).
try:
    import fcntl
    import pty
    import signal
    import termios

    _PTY_AVAILABLE = True
except ImportError:  # pragma: no cover - Windows / non-POSIX
    _PTY_AVAILABLE = False

log = logging.getLogger("polynoia.terminal")

router = APIRouter()

_READ_CHUNK = 65536


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    """Apply terminal dimensions to the pty so full-screen apps (vim, top,
    git log pager) lay out correctly."""
    rows = max(1, min(rows, 1000))
    cols = max(1, min(cols, 1000))
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    with contextlib.suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def _spawn_shell(cwd: str) -> tuple[int, int]:
    """Fork an interactive shell attached to a fresh pty.

    Returns ``(child_pid, master_fd)``. ``pty.fork`` makes the child a session
    leader with the slave as its controlling terminal, so job control (Ctrl-C,
    fg/bg) works. The child execs immediately, so the post-fork window is just
    chdir + execvp (no Python-level work that could deadlock on inherited locks).
    """
    pid, master_fd = pty.fork()
    if pid == 0:
        # ── CHILD ──────────────────────────────────────────────────
        with contextlib.suppress(OSError):
            os.chdir(cwd)
        os.environ["TERM"] = "xterm-256color"
        shell = os.environ.get("SHELL", "/bin/bash")
        try:
            os.execvp(shell, [shell])
        except OSError:
            os.execvp("/bin/sh", ["/bin/sh"])
        os._exit(127)  # unreachable on success
    # ── PARENT ──────────────────────────────────────────────────────
    os.set_blocking(master_fd, False)
    return pid, master_fd


def _reap(pid: int, master_fd: int) -> None:
    """Tear down the child shell + pty on disconnect (no zombies)."""
    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, 0)
    with contextlib.suppress(OSError):
        os.close(master_fd)


@router.websocket("/ws/workspaces/{ws_id}/terminal")
async def ws_terminal(websocket: WebSocket, ws_id: str):
    # No Unix PTY on this host (Windows): the interactive terminal can't run.
    # Close cleanly so the client shows a friendly "unavailable" instead of a
    # hard connection error, and the rest of the server stays fully functional.
    if not _PTY_AVAILABLE:
        await websocket.close(code=4501, reason="terminal unavailable on this platform")
        return
    # Validate it's a real workspace (don't materialize junk dirs from bogus
    # ids), then bootstrap its main dir on demand — a fresh project has no
    # sandbox on disk until the first agent runs, but the user can still open
    # a terminal in it.
    async with SessionLocal() as session:
        exists = await session.get(WorkspaceRow, ws_id)
    if exists is None:
        await websocket.close(code=4404, reason="unknown workspace")
        return
    try:
        root = await Sandbox.ensure_workspace(ws_id)
    except Exception as e:  # bootstrap failed (git missing, perms, …)
        log.warning("terminal bootstrap failed ws=%s: %s", ws_id, e)
        await websocket.close(code=4500, reason="workspace bootstrap failed")
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    pid, master_fd = _spawn_shell(str(root))
    log.info("terminal opened ws=%s pid=%s cwd=%s", ws_id, pid, root)

    # pty output → queue → WebSocket. add_reader fires when the master fd has
    # bytes; a single pump task preserves ordering and gives natural backpressure.
    out_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _on_readable() -> None:
        try:
            data = os.read(master_fd, _READ_CHUNK)
        except BlockingIOError:
            return
        except OSError:
            data = b""  # shell gone
        out_queue.put_nowait(data or None)  # None = EOF sentinel
        if not data:
            loop.remove_reader(master_fd)

    loop.add_reader(master_fd, _on_readable)

    async def pump_out() -> None:
        while True:
            data = await out_queue.get()
            if data is None:  # shell exited / fd closed
                return
            await websocket.send_bytes(data)

    async def pump_in() -> None:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                return
            payload = msg.get("bytes")
            if payload is not None:
                os.write(master_fd, payload)
                continue
            text = msg.get("text")
            if text:
                try:
                    ctrl = json.loads(text)
                except (ValueError, TypeError):
                    os.write(master_fd, text.encode("utf-8"))
                    continue
                if ctrl.get("type") == "resize":
                    _set_winsize(master_fd, int(ctrl.get("rows", 24)), int(ctrl.get("cols", 80)))

    out_task = asyncio.create_task(pump_out())
    in_task = asyncio.create_task(pump_in())
    try:
        # Whichever side ends first (shell exit or socket close) tears down both.
        await asyncio.wait({out_task, in_task}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in (out_task, in_task):
            t.cancel()
        with contextlib.suppress(ValueError, OSError):
            loop.remove_reader(master_fd)
        # _reap does a BLOCKING os.waitpid(pid, 0); running it inline on the
        # event loop froze the entire single-threaded uvloop (no new TCP accepts,
        # every endpoint 000s) when a killed PTY child was slow to reap. Offload
        # the kill+wait+close to a worker thread so the loop stays responsive.
        await loop.run_in_executor(None, _reap, pid, master_fd)
        with contextlib.suppress(RuntimeError):
            await websocket.close()
        log.info("terminal closed ws=%s pid=%s", ws_id, pid)
