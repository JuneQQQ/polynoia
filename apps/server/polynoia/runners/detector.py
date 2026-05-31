"""Project-type detection for the Docker-isolated live preview runner.

Pure filesystem inspection (no Docker, no network): read a workspace root and
decide HOW to run the whole project as a web app — static site / npm dev server
/ python web service — returning the image + container command + in-container
port. Detection order matters: a built `index.html` is an artifact, so a
package.json with a `dev` script wins over a stray index.html.

Python web services: the base python:slim image has NO web framework, and user
code like `app.run()` binds 127.0.0.1:5000 by default (unreachable from outside
the container). So we (a) install the detected framework even without a
requirements.txt, and (b) the runner injects a sitecustomize that forces
flask/werkzeug/uvicorn to 0.0.0.0:{container_port}. Django uses an explicit
`runserver 0.0.0.0:{port}` instead.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Port each kind listens on INSIDE the container (host port allocated separately).
STATIC_PORT = 8000
NPM_PORT = 5173
PY_PORT = 8000

_PY_ENTRIES = ("app.py", "main.py", "server.py", "wsgi.py", "asgi.py")
_PY_WEB_NEEDLES = (
    "flask", "fastapi", "django", "aiohttp", "bottle", "starlette",
    "app.run", "uvicorn", "werkzeug",
)


@dataclass
class ProjectPlan:
    kind: str            # "static" | "npm" | "python" | "unknown"
    image: str           # docker image ("" for unknown)
    cmd: list[str]       # container command
    container_port: int  # port the server listens on inside the container
    entry: str           # file that triggered detection (for the UI)
    needs_network: bool  # run needs egress (npm/pip install)
    note: str = ""       # human-facing note


def detect_project(root: Path, *, node_image: str, python_image: str) -> ProjectPlan:
    # 1) front-end工程: package.json with a dev script (highest priority).
    pkg = root / "package.json"
    if pkg.is_file():
        dev = _npm_dev_cmd(pkg)
        if dev:
            return ProjectPlan(
                kind="npm", image=node_image,
                cmd=["sh", "-c", f"npm install && {dev}"],
                container_port=NPM_PORT, entry="package.json", needs_network=True,
                note="前端工程:npm install + dev server",
            )

    # 2) python web service: app.py/main.py/... importing a web framework,
    #    or a Django manage.py.
    for fn in _PY_ENTRIES:
        f = root / fn
        if f.is_file() and _looks_like_web_py(f):
            return _python_plan(root, fn, _read(f), python_image)
    if (root / "manage.py").is_file():
        return _python_plan(root, "manage.py", _read(root / "manage.py"), python_image)

    # 3) static site: index.html, no build needed.
    if (root / "index.html").is_file():
        return ProjectPlan(
            kind="static", image=python_image,
            cmd=["python", "-m", "http.server", str(STATIC_PORT)],
            container_port=STATIC_PORT, entry="index.html", needs_network=False,
            note="静态站点",
        )

    return ProjectPlan(
        kind="unknown", image="", cmd=[], container_port=0, entry="",
        needs_network=False,
        note="没识别出可运行的项目(需要 index.html / package.json+dev 脚本 / app.py 等)",
    )


def _python_plan(root: Path, fn: str, src: str, image: str) -> ProjectPlan:
    """Build the run plan for a python web project: install the right framework
    (even without requirements.txt) and run it. host/port for flask/fastapi are
    forced to 0.0.0.0:{PY_PORT} by the runner's injected sitecustomize; Django
    gets an explicit runserver bind."""
    low = src.lower()
    is_django = "django" in low or fn == "manage.py" or (root / "manage.py").is_file()
    is_fastapi = "fastapi" in low or "uvicorn" in low
    is_flask = "flask" in low

    if (root / "requirements.txt").is_file():
        install = "pip install -r requirements.txt"
    else:
        # No requirements.txt: install the third-party packages the entry file
        # imports (flask_cors → flask-cors, etc.) PLUS the detected framework.
        # Best-effort — a real requirements.txt is still the reliable path.
        pkgs = set(_third_party_imports(src))
        if is_flask:
            pkgs.add("flask")
        if is_fastapi:
            pkgs.update(("fastapi", "uvicorn"))
        if is_django:
            pkgs.add("django")
        install = "pip install " + " ".join(sorted(pkgs or {"flask"}))

    if is_django:
        run = f"python manage.py runserver 0.0.0.0:{PY_PORT}"
        note = "Django 服务"
    elif is_fastapi and "uvicorn.run" not in low and "app.run" not in low:
        # bare ASGI module (defines `app`, never serves) → run uvicorn for it.
        mod = fn[:-3] if fn.endswith(".py") else fn
        run = f"uvicorn {mod}:app --host 0.0.0.0 --port {PY_PORT}"
        note = "FastAPI 服务"
    else:
        run = f"python {fn}"  # sitecustomize forces host/port for flask/uvicorn
        note = "FastAPI 服务" if is_fastapi else ("Flask 服务" if is_flask else "Python web 服务")

    return ProjectPlan(
        kind="python", image=image,
        cmd=["sh", "-c", f"{install} && {run}"],
        container_port=PY_PORT, entry=fn, needs_network=True, note=note,
    )


def _npm_dev_cmd(pkg: Path) -> str | None:
    """Return the dev command if package.json has a `scripts.dev`, forcing the
    dev server to bind 0.0.0.0 + a known port so it's reachable from outside the
    container. (vite/webpack accept `-- --host/--port`; harmless otherwise.)"""
    import json

    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = data.get("scripts")
    if not isinstance(scripts, dict) or "dev" not in scripts:
        return None
    return f"npm run dev -- --host 0.0.0.0 --port {NPM_PORT}"


def _read(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _third_party_imports(src: str) -> list[str]:
    """Top-level non-stdlib modules imported by `src`, as pip package names
    (best-effort): `from flask_cors import CORS` → "flask_cors" (pip normalizes
    to flask-cors). Misses import-name≠package-name cases (PIL→Pillow, cv2→…) —
    those still need a requirements.txt."""
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    mods: set[str] = set()
    for line in src.splitlines():
        m = re.match(r"\s*(?:import|from)\s+([A-Za-z_][\w]*)", line)
        if m and m.group(1) != "__future__" and m.group(1) not in stdlib:
            mods.add(m.group(1))
    return sorted(mods)


def _looks_like_web_py(f: Path) -> bool:
    return any(n in _read(f).lower() for n in _PY_WEB_NEEDLES)
