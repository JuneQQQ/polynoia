from __future__ import annotations

from polynoia import skills
from polynoia.context.identity import build_identity_layer
from polynoia.domain.entities import Agent, AgentSetup, AgentSkill
from polynoia.sandbox._core import Sandbox


def test_list_skills_includes_bundled_skills(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", tmp_path / "skills")

    listed = skills.list_skills()
    names = {s["name"] for s in listed}

    assert len(listed) >= 10
    assert {
        "superpower",
        "ppt-master",
        "excel-analyst",
        "docx-writer",
        "frontend-design",
        "backend-architect",
        "data-analyst",
        "code-review",
        "research-synthesizer",
        "test-engineer",
    }.issubset(names)
    assert next(s for s in listed if s["name"] == "ppt-master")["builtin"] is True


def test_installed_skill_overrides_bundled_skill(tmp_path, monkeypatch) -> None:
    installed = tmp_path / "skills"
    custom = installed / "ppt-master"
    custom.mkdir(parents=True)
    (custom / "SKILL.md").write_text(
        "---\nname: ppt-master\ndescription: Custom deck skill\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", installed)

    match = next(s for s in skills.list_skills() if s["name"] == "ppt-master")

    assert match["description"] == "Custom deck skill"
    assert match["builtin"] is False
    assert match["path"] == str(custom)


async def test_sandbox_places_bundled_skill(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", tmp_path / "missing-skills")
    sandbox = Sandbox(
        root=tmp_path / "sandbox",
        conv_id="conv-test",
    )

    placed = await sandbox.place_skill_packages(["ppt-master"], adapter_id="claudeCode")

    dest = (
        tmp_path
        / "sandbox"
        / ".polynoia"
        / "credentials"
        / ".claude"
        / "skills"
        / "ppt-master"
        / "SKILL.md"
    )
    assert placed == ["ppt-master"]
    assert dest.is_file()


async def test_sandbox_places_complete_packages_in_native_adapter_paths(
    tmp_path, monkeypatch
) -> None:
    installed = tmp_path / "skills"
    package = installed / "demo-skill"
    (package / "scripts").mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo\n---\nInstructions\n",
        encoding="utf-8",
    )
    (package / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", installed)
    sandbox = Sandbox(root=tmp_path / "sandbox", conv_id="conv-test", agent_id="agent-a")

    for adapter_id, suffix in (
        ("codex", (".agents", "skills")),
        ("opencoder", (".config", "opencode", "skills")),
    ):
        placed = await sandbox.place_skill_packages(["demo-skill"], adapter_id=adapter_id)
        root = sandbox.agent_runtime_home(adapter_id).joinpath(*suffix)
        assert placed == ["demo-skill"]
        assert (root / "demo-skill" / "SKILL.md").is_file()
        assert (root / "demo-skill" / "scripts" / "run.py").is_file()


async def test_skill_placement_uses_canonical_name_and_syncs_private_home(
    tmp_path, monkeypatch
) -> None:
    installed = tmp_path / "skills"
    package = installed / "demo-skill"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", installed)
    sandbox = Sandbox(root=tmp_path / "sandbox", conv_id="conv-test", agent_id="agent-a")

    await sandbox.place_skill_packages(["../../demo-skill"], adapter_id="codex")
    root = sandbox.native_skill_root("codex")
    assert (root / "demo-skill" / "SKILL.md").is_file()
    assert not (tmp_path / "demo-skill").exists()

    await sandbox.place_skill_packages([], adapter_id="codex")
    assert not root.exists()


def test_agent_runtime_home_is_contact_scoped(tmp_path) -> None:
    first = Sandbox(
        root=tmp_path / "sandbox",
        conv_id="shared-conv",
        agent_id="contact-a",
    )
    second = Sandbox(
        root=tmp_path / "sandbox",
        conv_id="shared-conv",
        agent_id="contact-b",
    )

    assert first.agent_runtime_home("codex") != second.agent_runtime_home("codex")


def test_adapter_without_native_skills_keeps_inline_fallback(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", tmp_path / "skills")
    agent = Agent(
        name="Fallback Agent",
        provider="future",
        handle="@fallback",
        initials="FA",
        color="#000",
        bg="#fff",
        setup=AgentSetup(adapter_id="future-adapter", model="future-model"),
        skills=[AgentSkill(name="ppt-master", instructions="")],
    )

    layer = build_identity_layer(agent)

    assert "# PPT Master" in layer.content


def test_remove_skill_does_not_remove_bundled_skill(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", tmp_path / "skills")
    bundled = skills.BUILTIN_SKILLS_DIR / "ppt-master"
    before = bundled / "SKILL.md"

    assert skills.remove_skill("ppt-master") is False
    assert before.is_file()
