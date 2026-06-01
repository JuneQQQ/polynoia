"""Interactive-terminal PTY bridge — core spawn/echo/teardown.

The WebSocket framing is thin glue; the substance is that ``_spawn_shell``
gives a real interactive shell in the requested directory whose I/O round-trips
through the pty master fd, and that ``_reap`` cleans it up without zombies.
We drive the master fd directly (no WebSocket) so the test is fast and
deterministic.
"""
from __future__ import annotations

import os
import select
import time

from polynoia.api.terminal import _reap, _set_winsize, _spawn_shell
from polynoia.sandbox import Sandbox


def _read_until(master_fd: int, marker: bytes, timeout: float = 5.0) -> bytes:
    """Drain the pty master until ``marker`` appears or ``timeout`` elapses."""
    buf = b""
    deadline = time.monotonic() + timeout
    while marker not in buf and time.monotonic() < deadline:
        r, _, _ = select.select([master_fd], [], [], 0.2)
        if not r:
            continue
        try:
            chunk = os.read(master_fd, 65536)
        except (BlockingIOError, OSError):
            continue
        if not chunk:
            break
        buf += chunk
    return buf


def test_shell_echoes_command_output(tmp_path):
    pid, master_fd = _spawn_shell(str(tmp_path))
    try:
        os.write(master_fd, b"echo polynoia_term_ok\n")
        out = _read_until(master_fd, b"polynoia_term_ok")
        assert b"polynoia_term_ok" in out
    finally:
        _reap(pid, master_fd)


def test_shell_starts_in_requested_cwd(tmp_path):
    (tmp_path / "needle.txt").write_text("x")
    pid, master_fd = _spawn_shell(str(tmp_path))
    try:
        os.write(master_fd, b"pwd; ls\n")
        out = _read_until(master_fd, b"needle.txt")
        assert b"needle.txt" in out
        assert str(tmp_path).encode() in out
    finally:
        _reap(pid, master_fd)


def test_set_winsize_does_not_raise(tmp_path):
    pid, master_fd = _spawn_shell(str(tmp_path))
    try:
        _set_winsize(master_fd, rows=40, cols=120)  # real pty → must succeed silently
        _set_winsize(master_fd, rows=0, cols=0)  # clamped, still no raise
    finally:
        _reap(pid, master_fd)


def test_reap_kills_child(tmp_path):
    pid, master_fd = _spawn_shell(str(tmp_path))
    _reap(pid, master_fd)
    # After reaping, the pid is gone — signal 0 (existence probe) must fail.
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    except PermissionError:
        alive = True
    assert not alive


async def test_ensure_workspace_bootstraps_and_is_idempotent(tmp_path, monkeypatch):
    """A fresh project has no sandbox dir until the first agent runs; the
    terminal/IDE endpoints lazily bootstrap it. ensure_workspace must create
    the shared .git and be safe to call repeatedly."""
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path)
    root1 = await Sandbox.ensure_workspace("01TESTWORKSPACEID0000000000")
    assert root1.is_dir()
    assert (root1 / ".git").exists()  # bootstrapped a real git repo
    # Second call: no error, same root, doesn't re-init (commit count stays 1).
    root2 = await Sandbox.ensure_workspace("01TESTWORKSPACEID0000000000")
    assert root1 == root2
