from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from polynoia_a2a_bridge.sdk_contract import A2A_TCK_COMMIT
from tools import run_pinned_tck as runner
from tools.run_pinned_tck import (
    audit_junit_skips,
    build_tck_command,
    corrected_tck_checkout,
    verify_tck_checkout,
    write_summary,
)


def test_build_tck_command_is_jsonrpc_must(tmp_path: Path) -> None:
    command = build_tck_command(tmp_path, "http://127.0.0.1:9999")
    assert command == [
        "uv",
        "run",
        "./run_tck.py",
        "--sut-host",
        "http://127.0.0.1:9999",
        "--transport",
        "jsonrpc",
        "--level",
        "must",
    ]


def test_verify_tck_checkout_rejects_wrong_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "run_tck.py").write_text("#!/usr/bin/env python3\n")
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *args, **kwargs: "deadbeef\n",
    )
    with pytest.raises(RuntimeError, match="TCK checkout mismatch"):
        verify_tck_checkout(tmp_path)


def test_verify_tck_checkout_rejects_dirty_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "run_tck.py").write_text("#!/usr/bin/env python3\n")

    def check_output(command: list[str], **_kwargs: object) -> str:
        return A2A_TCK_COMMIT if command[-2:] == ["rev-parse", "HEAD"] else " M tck/file.py"

    monkeypatch.setattr(subprocess, "check_output", check_output)

    with pytest.raises(RuntimeError, match="must be clean"):
        verify_tck_checkout(tmp_path)


def test_summary_discloses_the_pinned_tck_harness_correction(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"

    write_summary(
        summary,
        command=["uv", "run", "./run_tck.py"],
        counts=(81, 0, 0, 154),
        exit_code=0,
        skip_counts={
            "transport-filtered": 112,
            "push-disabled": 30,
            "extended-card-disabled": 6,
            "inverse-streaming": 4,
            "required-extension-disabled": 2,
        },
    )

    rendered = summary.read_text()
    assert "CORE-SEND-003" in rendered
    assert "harness correction" in rendered
    assert "Applicable JSON-RPC MUST skips: `0`" in rendered
    assert not any(line.endswith(" ") for line in rendered.splitlines())


def test_summary_rejects_skip_count_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="skip classification mismatch"):
        write_summary(
            tmp_path / "summary.md",
            command=["uv", "run", "./run_tck.py"],
            counts=(2, 0, 0, 2),
            exit_code=0,
            skip_counts={
                "transport-filtered": 1,
                "push-disabled": 0,
                "extended-card-disabled": 0,
                "inverse-streaming": 0,
                "required-extension-disabled": 0,
            },
        )


def test_skip_audit_classifies_only_reviewed_reasons(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<testsuite tests="5" skipped="5">
        <testcase name="transport"><skipped message="Transport 'grpc' not configured" /></testcase>
        <testcase name="push"><skipped message="Agent does not support push notifications" /></testcase>
        <testcase name="card"><skipped message="Agent does not declare extendedAgentCard capability" /></testcase>
        <testcase name="stream"><skipped message="Agent supports streaming; cannot trigger -32004" /></testcase>
        <testcase name="extension"><skipped message="Agent card does not declare urn:a2a:tck:required-extension as required" /></testcase>
        </testsuite>"""
    )

    assert audit_junit_skips(junit) == {
        "transport-filtered": 1,
        "push-disabled": 1,
        "extended-card-disabled": 1,
        "inverse-streaming": 1,
        "required-extension-disabled": 1,
    }


def test_skip_audit_rejects_unreviewed_reason(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<testsuite tests="1" skipped="1">
        <testcase classname="jsonrpc" name="must"><skipped message="new applicable skip" /></testcase>
        </testsuite>"""
    )

    with pytest.raises(RuntimeError, match="Unreviewed TCK skip"):
        audit_junit_skips(junit)


def test_corrected_checkout_reverts_when_body_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        runner,
        "apply_tck_harness_correction",
        lambda _path: calls.append("apply"),
    )
    monkeypatch.setattr(
        runner,
        "revert_tck_harness_correction",
        lambda _path: calls.append("revert"),
    )

    with pytest.raises(RuntimeError, match="cleanup failed"), corrected_tck_checkout(tmp_path):
        raise RuntimeError("cleanup failed")

    assert calls == ["apply", "revert"]
