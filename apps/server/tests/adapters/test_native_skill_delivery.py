from __future__ import annotations

from pathlib import Path

from polynoia.adapters.claude_code import ClaudeCodeAdapter
from polynoia.adapters.codex import CodexAdapter
from polynoia.adapters.opencode import OpenCodeAdapter


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
        skills=["demo-skill"],
    )
    try:
        runtime_home = session._sandbox.agent_runtime_home("opencoder")
        assert (
            runtime_home
            / ".config"
            / "opencode"
            / "skills"
            / "demo-skill"
            / "scripts"
            / "run.py"
        ).is_file()
    finally:
        await session.close()
