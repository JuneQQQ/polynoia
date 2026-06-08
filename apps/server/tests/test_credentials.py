from __future__ import annotations

from pathlib import Path

from polynoia.credentials import (
    credential_plan,
    credential_source_home,
    sync_codex_home,
    use_direct_host_credentials,
)


def test_credential_source_home_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POLYNOIA_CRED_HOME", str(tmp_path))

    assert credential_source_home() == tmp_path


def test_direct_credentials_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POLYNOIA_CRED_HOME", str(tmp_path))
    monkeypatch.setenv("POLYNOIA_DIRECT_CREDS", "1")

    plan = credential_plan()
    assert plan.mode == "direct"
    assert plan.source_home == tmp_path
    assert use_direct_host_credentials()


def test_sandbox_copy_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POLYNOIA_CRED_HOME", str(tmp_path))
    monkeypatch.setenv("POLYNOIA_DIRECT_CREDS", "0")

    plan = credential_plan()
    assert plan.mode == "sandbox-copy"
    assert plan.source_home == tmp_path
    assert not use_direct_host_credentials()


def test_sync_codex_home_copies_only_runtime_allowlist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    host = tmp_path / "host"
    codex = host / ".codex"
    codex.mkdir(parents=True)
    (codex / "config.toml").write_text('model = "gpt-5.5"\n')
    (codex / "auth.json").write_text('{"token":"secret"}\n')
    (codex / "history.jsonl").write_text("do not copy\n")
    monkeypatch.setenv("POLYNOIA_CRED_HOME", str(host))

    dst = tmp_path / "sandbox" / ".codex"
    sync_codex_home(dst)

    assert (dst / "config.toml").read_text() == 'model = "gpt-5.5"\n'
    assert (dst / "auth.json").read_text() == '{"token":"secret"}\n'
    assert not (dst / "history.jsonl").exists()

