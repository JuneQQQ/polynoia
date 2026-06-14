"""Regression for the stress-harness pure logic (git invariants + real
acceptance verifiers) — locks behavior without needing live agents.

The harness scripts live in scripts/testkit/; we import them by path.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# .../apps/server/tests/api/test_stress_harness.py → repo root is parents[4]
REPO = Path(__file__).resolve().parents[4]
TESTKIT = REPO / "scripts" / "testkit"
sys.path.insert(0, str(TESTKIT))
# contention.py also imports check_invariants from the server-side testkit dir
sys.path.insert(0, str(REPO / "apps" / "server" / "scripts" / "testkit"))


def _git(d: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "ws"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / "a.txt").write_text("hello\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    return d


# ── contention.git_invariants ────────────────────────────────────────


def test_git_invariants_clean_repo_passes(repo, monkeypatch):
    import contention

    monkeypatch.setattr(contention, "SANDBOX_WS", repo.parent)
    assert contention.git_invariants(repo.name) == []


def test_git_invariants_flags_conflict_markers(repo, monkeypatch):
    import contention

    monkeypatch.setattr(contention, "SANDBOX_WS", repo.parent)
    (repo / "a.txt").write_text("<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> other\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "bad")
    bad = contention.git_invariants(repo.name)
    assert any("conflict markers" in b for b in bad)


def test_git_invariants_flags_half_merge(repo, monkeypatch):
    import contention

    monkeypatch.setattr(contention, "SANDBOX_WS", repo.parent)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    (repo / ".git" / "MERGE_HEAD").write_text(head + "\n")
    bad = contention.git_invariants(repo.name)
    assert any("MERGE_HEAD" in b for b in bad)


def test_git_invariants_missing_workspace(tmp_path, monkeypatch):
    import contention

    monkeypatch.setattr(contention, "SANDBOX_WS", tmp_path)
    bad = contention.git_invariants("nonexistent")
    assert bad and "not materialized" in bad[0]


# ── benchmarks real verifiers ────────────────────────────────────────


def test_fullstack_verifier_rejects_empty_stub(tmp_path):
    import benchmarks

    ws = tmp_path / "stub"
    ws.mkdir()
    (ws / "README.md").write_text("# TODO\n" + "x" * 1100)  # 1KB+ but no real code
    res = benchmarks.verify("fullstack_issue_tracker", ws)
    # generic "has content" may pass, but the real checks (React pkg, FastAPI,
    # CRUD routes, py-compile) must fail → low score, NOT a pass.
    assert res["score"] < 0.6
    names = {c["name"]: c["ok"] for c in res["checks"]}
    assert names.get("后端 FastAPI app") is False
    assert names.get("前端是 React 工程") is False


def test_fullstack_verifier_passes_real_structure(tmp_path):
    import benchmarks

    ws = tmp_path / "real"
    (ws / "app").mkdir(parents=True)
    (ws / "app" / "package.json").write_text(
        '{"dependencies":{"react":"^18"},"scripts":{"build":"vite build"}}'
    )
    (ws / "app" / "src").mkdir()
    (ws / "app" / "src" / "App.jsx").write_text("export default function App(){return null}")
    (ws / "backend").mkdir()
    (ws / "backend" / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
        "@app.get('/issues')\ndef a(): return []\n"
        "@app.post('/issues')\ndef b(): return {}\n"
        "@app.delete('/issues/{i}')\ndef c(i): return {}\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "edit issues (+10/-0)"], check=True, capture_output=True)
    res = benchmarks.verify("fullstack_issue_tracker", ws)
    names = {c["name"]: c["ok"] for c in res["checks"]}
    assert names["前端是 React 工程"] is True
    assert names["后端 FastAPI app"] is True
    assert names["后端有 CRUD 路由(≥3)"] is True
    assert names["后端 Python 全部可编译"] is True


def test_py_compile_catches_syntax_error(tmp_path):
    import benchmarks

    ws = tmp_path / "broken"
    ws.mkdir()
    (ws / "x.py").write_text("def f(:\n  pass\n")  # syntax error
    ok, msg = benchmarks._py_all_compile(ws)
    assert ok is False and "语法错" in msg
