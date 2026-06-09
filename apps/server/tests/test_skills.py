from __future__ import annotations

from polynoia import skills
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

    await sandbox.place_skill_packages(["ppt-master"])

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
    assert dest.is_file()


def test_remove_skill_does_not_remove_bundled_skill(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("polynoia.settings.settings.skills_dir", tmp_path / "skills")
    bundled = skills.BUILTIN_SKILLS_DIR / "ppt-master"
    before = bundled / "SKILL.md"

    assert skills.remove_skill("ppt-master") is False
    assert before.is_file()
