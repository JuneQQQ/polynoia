"""Docker-isolated project runner: run the WHOLE workspace project as a web app
inside a container and expose it for iframe preview.

We shell out to the `docker` CLI (no docker-py dependency) via asyncio. One
container per workspace, named ``polynoia-run-<ws>``. Host ports bind to
127.0.0.1 only; the iframe connects directly to ``localhost:{host_port}``
(Docker Desktop forwards it to the host). Resource-limited (mem/cpu/pids) so a
runaway project can't take down the host — the isolation CLAUDE.md §6.2 wants.

A stale same-named container is force-removed before each start, so re-running
is idempotent. Containers are NOT --rm (dev servers don't exit); stop() removes.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
from dataclasses import dataclass
from pathlib import Path

import httpx

from polynoia.runners.detector import ProjectPlan, detect_project
from polynoia.settings import settings

# Injected into python containers via PYTHONPATH=/polynoia-boot so a user's
# `app.run()` / werkzeug / uvicorn binds 0.0.0.0:{POLYNOIA_RUN_PORT} instead of
# the default 127.0.0.1:5000 — otherwise the dev server is unreachable from
# outside the container. Best-effort: absent frameworks are silently skipped.
_BOOT_SITECUSTOMIZE = '''import os as _os
_HOST = "0.0.0.0"
_PORT = int(_os.environ.get("POLYNOIA_RUN_PORT", "8000"))

try:
    import flask as _flask
    _flask_run = _flask.Flask.run
    def _patched_flask_run(self, host=None, port=None, *a, **k):
        return _flask_run(self, _HOST, _PORT, *a, **k)
    _flask.Flask.run = _patched_flask_run
except Exception:
    pass

try:
    import werkzeug.serving as _ws
    _run_simple = _ws.run_simple
    def _patched_run_simple(hostname, port, application, *a, **k):
        return _run_simple(_HOST, _PORT, application, *a, **k)
    _ws.run_simple = _patched_run_simple
except Exception:
    pass

try:
    import uvicorn as _uvicorn
    _uvicorn_run = _uvicorn.run
    def _patched_uvicorn_run(app, *a, **k):
        k["host"] = _HOST
        k["port"] = _PORT
        return _uvicorn_run(app, *a, **k)
    _uvicorn.run = _patched_uvicorn_run
except Exception:
    pass
'''


def _ensure_boot_dir() -> Path:
    """Write the python boot sitecustomize once; return its dir (to mount)."""
    d = settings.sandbox_root / ".runner-boot"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "sitecustomize.py"
    if not f.is_file() or f.read_text(encoding="utf-8") != _BOOT_SITECUSTOMIZE:
        f.write_text(_BOOT_SITECUSTOMIZE, encoding="utf-8")
    return d


async def _docker(*args: str, timeout: float = 60.0) -> tuple[int, str, str]:
    """Run a docker CLI command, return (rc, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return (127, "", "docker not found on PATH")
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return (124, "", "docker command timed out")
    return (proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))


def _free_port(base: int, span: int) -> int:
    for p in range(base, base + span):
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free host port for the project runner")


async def _http_ok(port: int) -> bool:
    """Any HTTP response (even 4xx/5xx) means the in-container server is UP. A
    plain TCP check is NOT enough: docker-proxy holds the host port open before
    the container's server listens, which would falsely read 'running' while the
    app is still e.g. `pip install`-ing."""
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            await client.get(f"http://127.0.0.1:{port}/")
        return True
    except Exception:
        return False


@dataclass
class Runner:
    ws_id: str
    plan: ProjectPlan
    container: str
    host_port: int
    status: str = "starting"  # starting | running | error | stopped
    error: str = ""

    def public(self) -> dict:
        return {
            "ws_id": self.ws_id,
            "kind": self.plan.kind,
            "entry": self.plan.entry,
            "note": self.plan.note,
            "status": self.status,
            "error": self.error,
            "host_port": self.host_port,
            # iframe points here (Docker Desktop forwards 127.0.0.1:{port} to host)
            "url": f"http://localhost:{self.host_port}/" if self.host_port else "",
        }


class RunnerManager:
    """Process-wide singleton: one running container per workspace."""

    def __init__(self) -> None:
        self._runners: dict[str, Runner] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def container_name(ws_id: str) -> str:
        return f"polynoia-run-{ws_id[:16].lower()}"

    async def docker_available(self) -> bool:
        rc, _o, _e = await _docker("version", "--format", "{{.Server.Version}}", timeout=8)
        return rc == 0

    def get(self, ws_id: str) -> Runner | None:
        return self._runners.get(ws_id)

    async def start(self, ws_id: str, root: Path) -> Runner:
        async with self._lock:
            existing = self._runners.get(ws_id)
            if existing and existing.status in ("starting", "running"):
                return existing

            plan = detect_project(
                root,
                node_image=settings.runner_node_image,
                python_image=settings.runner_python_image,
            )
            if plan.kind == "unknown":
                r = Runner(ws_id, plan, "", 0, status="error", error=plan.note)
                self._runners[ws_id] = r
                return r

            if not await self.docker_available():
                r = Runner(ws_id, plan, "", 0, status="error",
                           error="Docker 不可用(未安装或未启动)")
                self._runners[ws_id] = r
                return r

            name = self.container_name(ws_id)
            await _docker("rm", "-f", name, timeout=20)  # clear stale
            host_port = _free_port(settings.runner_port_base, settings.runner_port_span)
            extra: list[str] = []
            if plan.kind == "python":
                boot = _ensure_boot_dir()
                extra = [
                    "-v", f"{boot}:/polynoia-boot:ro",
                    "-e", "PYTHONPATH=/polynoia-boot",
                    "-e", f"POLYNOIA_RUN_PORT={plan.container_port}",
                ]
            args = [
                "run", "-d", "--name", name,
                "-p", f"127.0.0.1:{host_port}:{plan.container_port}",
                "-v", f"{root}:/app", "-w", "/app",
                *extra,
                "--memory", settings.runner_memory,
                "--cpus", settings.runner_cpus,
                "--pids-limit", "512",
                plan.image, *plan.cmd,
            ]
            rc, out, err = await _docker(*args, timeout=120)
            if rc != 0:
                r = Runner(ws_id, plan, name, host_port, status="error",
                           error=(err or out).strip()[:600])
                self._runners[ws_id] = r
                return r
            r = Runner(ws_id, plan, name, host_port, status="starting")
            self._runners[ws_id] = r
            return r

    async def status(self, ws_id: str) -> Runner | None:
        r = self._runners.get(ws_id)
        if r is None or not r.container:
            return r
        rc, out, _e = await _docker(
            "inspect", "-f", "{{.State.Running}}", r.container, timeout=10
        )
        if rc != 0:
            r.status = "stopped"
            return r
        running = out.strip().startswith("true")
        if not running:
            # container exited — surface its tail as the error
            r.status = "error" if r.status in ("starting", "running") else r.status
            if not r.error:
                _rc, logs_out, logs_err = await _docker(
                    "logs", "--tail", "40", r.container, timeout=10
                )
                r.error = (logs_out + logs_err).strip()[:600] or "容器已退出"
        elif r.status == "starting" and await _http_ok(r.host_port):
            r.status = "running"
        return r

    async def logs(self, ws_id: str, tail: int = 200) -> str:
        r = self._runners.get(ws_id)
        if r is None or not r.container:
            return ""
        rc, out, err = await _docker("logs", "--tail", str(tail), r.container, timeout=15)
        return (out + err) if rc == 0 else err

    async def stop(self, ws_id: str) -> None:
        async with self._lock:
            r = self._runners.pop(ws_id, None)
        if r and r.container:
            await _docker("rm", "-f", r.container, timeout=30)

    async def stop_all(self) -> None:
        async with self._lock:
            runners = list(self._runners.values())
            self._runners.clear()
        for r in runners:
            if r.container:
                await _docker("rm", "-f", r.container, timeout=30)


_manager: RunnerManager | None = None


def get_runner_manager() -> RunnerManager:
    global _manager
    if _manager is None:
        _manager = RunnerManager()
    return _manager
