from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from polynoia.adapters.claude_code import ClaudeCodeAdapter
from polynoia.adapters.codex import CodexAdapter
from polynoia.adapters.opencode import OpenCodeAdapter, _opencode_config_content
from polynoia.sandbox import Sandbox


def _install_test_skill(root: Path) -> None:
    skill = root / "demo-skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Native delivery test\n---\nUse the script.\n",
        encoding="utf-8",
    )
    (skill / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")


async def test_claude_session_enables_only_bound_native_skills(
    tmp_path: Path, monkeypatch
) -> None:
    skills_dir = tmp_path / "installed"
    _install_test_skill(skills_dir)
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", skills_dir)
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sandboxes")

    session = await ClaudeCodeAdapter().start_session(
        conv_id="claude-skills",
        skills=["demo-skill", "missing-skill"],
    )
    try:
        assert session._opts.skills == ["demo-skill"]
        assert (
            session._sandbox.native_skill_root("claudeCode")
            / "demo-skill"
            / "scripts"
            / "run.py"
        ).is_file()
    finally:
        await session.close()


async def test_codex_session_uses_contact_scoped_home_for_native_skills(
    tmp_path: Path, monkeypatch
) -> None:
    skills_dir = tmp_path / "installed"
    _install_test_skill(skills_dir)
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", skills_dir)
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sandboxes")

    session = await CodexAdapter().start_session(
        conv_id="codex-skills",
        agent_id="contact-a",
        skills=["demo-skill"],
    )
    try:
        runtime_home = session._sandbox.agent_runtime_home("codex")
        env = session._env()
        assert env["HOME"] == str(runtime_home)
        assert env["USERPROFILE"] == str(runtime_home)
        assert env["CODEX_HOME"] == session._codex_home
        assert (
            runtime_home / ".agents" / "skills" / "demo-skill" / "scripts" / "run.py"
        ).is_file()
    finally:
        await session.close()


async def test_opencode_session_uses_contact_scoped_native_skill_path(
    tmp_path: Path, monkeypatch
) -> None:
    skills_dir = tmp_path / "installed"
    _install_test_skill(skills_dir)
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", skills_dir)
    monkeypatch.setattr("polynoia.settings.settings.sandbox_root", tmp_path / "sandboxes")

    session = await OpenCodeAdapter().start_session(
        conv_id="opencode-skills",
        agent_id="contact-a",
        skills=["demo-skill", "missing-skill"],
    )
    try:
        runtime_home = session._sandbox.agent_runtime_home("opencoder")
        assert session._skills == ["demo-skill"]
        assert (
            runtime_home
            / ".config"
            / "opencode"
            / "skills"
            / "demo-skill"
            / "scripts"
            / "run.py"
        ).is_file()
        config = json.loads(_opencode_config_content(None, session._skills))
        assert config["permission"]["skill"] == {
            "*": "deny",
            "demo-skill": "allow",
        }
        monkeypatch.setattr(
            "polynoia.adapters.opencode._polynoia_opencode_data_home",
            lambda: str(tmp_path / "opencode-data"),
        )
        env = session._prepare_subprocess_env()
        written = json.loads(Path(env["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))
        assert written["permission"]["skill"] == {
            "*": "deny",
            "demo-skill": "allow",
        }
        assert json.loads(env["OPENCODE_CONFIG_CONTENT"]) == written

        # The idempotent guard must not replace a running subprocess.
        running_marker = SimpleNamespace(returncode=None)
        session._proc = running_marker
        session._connection = object()
        await session._ensure_subprocess()
        assert session._proc is running_marker
        session._proc = None
        session._connection = None

    finally:
        session._proc = None
        await session.close()


async def test_opencode_workspace_session_keeps_bound_skill_allowlist(
    tmp_path: Path, monkeypatch
) -> None:
    skills_dir = tmp_path / "installed"
    _install_test_skill(skills_dir)
    sandbox = Sandbox(
        root=tmp_path / "workspace-agent",
        workspace_root=tmp_path / "workspace",
        conv_id="workspace-conv",
        agent_id="contact-a",
    )

    async def _workspace_sandbox(**_kwargs) -> Sandbox:
        return sandbox

    monkeypatch.setattr("polynoia.settings.settings.skills_dir", skills_dir)
    monkeypatch.setattr(Sandbox, "create_workspace_sandbox", _workspace_sandbox)

    session = await OpenCodeAdapter().start_session(
        conv_id="workspace-conv",
        workspace_id="workspace-a",
        agent_id="contact-a",
        skills=["demo-skill"],
    )
    try:
        assert session._skills == ["demo-skill"]
    finally:
        await session.close()
