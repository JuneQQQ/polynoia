"""Project-type detection tests (pure, no Docker)."""
from __future__ import annotations

from pathlib import Path

from polynoia.runners.detector import detect_project

NODE = "node:20-slim"
PY = "python:3.12-slim"


def _mk(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def test_static_index_html(tmp_path: Path) -> None:
    _mk(tmp_path, {"index.html": "<h1>hi</h1>"})
    p = detect_project(tmp_path, node_image=NODE, python_image=PY)
    assert p.kind == "static"
    assert p.image == PY
    assert "http.server" in " ".join(p.cmd)
    assert p.needs_network is False


def test_npm_dev_wins_over_index_html(tmp_path: Path) -> None:
    _mk(tmp_path, {"index.html": "x", "package.json": '{"scripts":{"dev":"vite"}}'})
    p = detect_project(tmp_path, node_image=NODE, python_image=PY)
    assert p.kind == "npm"
    assert p.image == NODE
    joined = " ".join(p.cmd)
    assert "npm install" in joined and "--host 0.0.0.0" in joined
    assert p.needs_network is True


def test_npm_without_dev_script_falls_through_to_static(tmp_path: Path) -> None:
    _mk(tmp_path, {"index.html": "x", "package.json": '{"scripts":{"build":"vite build"}}'})
    p = detect_project(tmp_path, node_image=NODE, python_image=PY)
    assert p.kind == "static"  # no dev script → not runnable as npm dev


def test_python_web_app(tmp_path: Path) -> None:
    _mk(tmp_path, {"app.py": "from flask import Flask\napp=Flask(__name__)\napp.run()"})
    p = detect_project(tmp_path, node_image=NODE, python_image=PY)
    assert p.kind == "python"
    assert "python app.py" in " ".join(p.cmd)


def test_python_with_requirements_installs_first(tmp_path: Path) -> None:
    _mk(tmp_path, {"main.py": "import fastapi\n", "requirements.txt": "fastapi\nuvicorn"})
    p = detect_project(tmp_path, node_image=NODE, python_image=PY)
    assert p.kind == "python"
    assert "pip install -r requirements.txt" in " ".join(p.cmd)
    assert p.needs_network is True


def test_plain_python_script_not_web(tmp_path: Path) -> None:
    _mk(tmp_path, {"app.py": "print('hello world')"})
    p = detect_project(tmp_path, node_image=NODE, python_image=PY)
    assert p.kind == "unknown"


def test_unknown_when_no_entry(tmp_path: Path) -> None:
    _mk(tmp_path, {"readme.md": "# docs"})
    p = detect_project(tmp_path, node_image=NODE, python_image=PY)
    assert p.kind == "unknown"
    assert p.image == ""
