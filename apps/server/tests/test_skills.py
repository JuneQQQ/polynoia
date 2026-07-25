from __future__ import annotations

from pathlib import Path

import pytest

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


async def test_non_portable_package_uses_inline_fallback_for_native_adapter(
    tmp_path, monkeypatch
) -> None:
    installed = tmp_path / "skills"
    package = installed / "legacy_skill"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: legacy_skill\ndescription: Legacy package\n---\n"
        "# Legacy instructions\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", installed)
    sandbox = Sandbox(root=tmp_path / "sandbox", conv_id="conv-test", agent_id="agent-a")
    agent = Agent(
        name="Native Agent",
        provider="codex",
        handle="@native",
        initials="NA",
        color="#000",
        bg="#fff",
        setup=AgentSetup(adapter_id="codex", model="test-model"),
        skills=[AgentSkill(name="legacy_skill", instructions="")],
    )

    placed = await sandbox.place_skill_packages(["legacy_skill"], adapter_id="codex")
    layer = build_identity_layer(agent)

    assert placed == []
    assert "# Legacy instructions" in layer.content


def test_copy_skill_package_rejects_symlinks(
    tmp_path: Path, monkeypatch
) -> None:
    package = tmp_path / "source"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: safe-skill\ndescription: Safe\n---\n",
        encoding="utf-8",
    )
    escaped = package / "linked-secret"
    escaped.write_text("secret", encoding="utf-8")
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == escaped or original_is_symlink(path),
    )

    with pytest.raises(ValueError, match="unsupported symlink"):
        skills.copy_skill_package(package, tmp_path / "destination")

    assert not (tmp_path / "destination").exists()


def test_unknown_native_skill_layout_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not support native skills"):
        skills.native_skill_layout("future-adapter")


async def test_install_local_skill_copies_complete_package(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: local-skill\ndescription: Local package\n---\n",
        encoding="utf-8",
    )
    (source / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    installed = tmp_path / "installed"
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", installed)

    result = await skills.install_skill(str(source))

    assert result[0]["name"] == "local-skill"
    assert (installed / "local-skill" / "scripts" / "run.py").is_file()


async def test_install_rejects_symlink_before_replacing_existing_package(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: guarded-skill\ndescription: Guarded package\n---\n",
        encoding="utf-8",
    )
    linked = source / "linked-secret"
    linked.write_text("secret", encoding="utf-8")
    installed = tmp_path / "installed"
    existing = installed / "guarded-skill"
    existing.mkdir(parents=True)
    marker = existing / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", installed)
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == linked or original_is_symlink(path),
    )

    with pytest.raises(ValueError, match="unsupported symlink"):
        await skills.install_skill(str(source))

    assert marker.read_text(encoding="utf-8") == "keep"


def test_remove_skill_does_not_remove_bundled_skill(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", tmp_path / "skills")
    bundled = skills.BUILTIN_SKILLS_DIR / "ppt-master"
    before = bundled / "SKILL.md"

    assert skills.remove_skill("ppt-master") is False
    assert before.is_file()
