from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path

import httpx

from polynoia_a2a_bridge.sdk_contract import A2A_SDK_VERSION, A2A_TCK_COMMIT

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BRIDGE_ROOT.parents[1]
DEFAULT_REPORT_DIR = REPOSITORY_ROOT / ".scratch/a2a-tck-phase1/reports"
DEFAULT_SUMMARY = REPOSITORY_ROOT / "docs/a2a-tck/2026-08-02-phase1-sdk-1.1.2.md"
TCK_HARNESS_PATCH = BRIDGE_ROOT / "tools/patches/a2a-tck-5996b79-core-send-003.patch"
_SKIP_CATEGORIES = (
    "transport-filtered",
    "push-disabled",
    "extended-card-disabled",
    "inverse-streaming",
    "required-extension-disabled",
)


def verify_tck_checkout(path: Path) -> None:
    if not (path / "run_tck.py").is_file():
        raise RuntimeError(f"TCK runner not found under {path}")
    head = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    if head != A2A_TCK_COMMIT:
        raise RuntimeError(f"TCK checkout mismatch: expected {A2A_TCK_COMMIT}, found {head}")
    status = subprocess.check_output(
        ["git", "-C", str(path), "status", "--porcelain"],
        text=True,
    ).strip()
    if status:
        raise RuntimeError(f"TCK checkout must be clean before testing; found: {status}")


def apply_tck_harness_correction(path: Path) -> None:
    command = ["git", "-C", str(path), "apply", str(TCK_HARNESS_PATCH)]
    subprocess.run([*command[:-1], "--check", command[-1]], check=True)
    subprocess.run(command, check=True)


def revert_tck_harness_correction(path: Path) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "apply",
            "--reverse",
            str(TCK_HARNESS_PATCH),
        ],
        check=True,
    )


@contextmanager
def corrected_tck_checkout(path: Path) -> Iterator[None]:
    apply_tck_harness_correction(path)
    try:
        yield
    finally:
        revert_tck_harness_correction(path)


def build_tck_command(path: Path, sut_url: str) -> list[str]:
    del path
    return [
        "uv",
        "run",
        "./run_tck.py",
        "--sut-host",
        sut_url,
        "--transport",
        "jsonrpc",
        "--level",
        "must",
    ]


def wait_for_card(sut_url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    card_url = f"{sut_url}/.well-known/agent-card.json"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"TCK SUT exited early with {process.returncode}")
        try:
            response = httpx.get(card_url, timeout=0.5)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"TCK SUT did not publish {card_url}")


def junit_counts(path: Path) -> tuple[int, int, int, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return tuple(
        sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    )


def _skip_category(reason: str) -> str | None:
    if reason in {
        "Transport 'grpc' not configured",
        "Transport 'grpc' not configured (filtered by --transport)",
        "Transport 'http_json' not configured",
        "Transport 'http_json' not configured (filtered by --transport)",
        "gRPC transport not configured",
        "HTTP+JSON transport not configured",
    }:
        return "transport-filtered"
    if reason == "Agent does not support push notifications":
        return "push-disabled"
    if reason in {
        "Agent does not declare extendedAgentCard capability",
        "Agent card does not declare capabilities for: ['agent-card']",
    }:
        return "extended-card-disabled"
    if reason in {
        "Agent supports streaming",
        "Agent supports streaming; cannot trigger -32004",
        "Agent supports streaming; cannot trigger UnsupportedOperationError",
    }:
        return "inverse-streaming"
    if reason == "Agent card does not declare urn:a2a:tck:required-extension as required":
        return "required-extension-disabled"
    return None


def audit_junit_skips(path: Path) -> dict[str, int]:
    counts = dict.fromkeys(_SKIP_CATEGORIES, 0)
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        marker = case.find("skipped")
        if marker is None:
            continue
        reason = marker.attrib.get("message", "")
        category = _skip_category(reason)
        if category is None:
            identity = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
            raise RuntimeError(f"Unreviewed TCK skip {identity}: {reason}")
        counts[category] += 1
    return counts


def write_summary(
    path: Path,
    *,
    command: list[str],
    counts: tuple[int, int, int, int],
    exit_code: int,
    skip_counts: Mapping[str, int],
) -> None:
    tests, failures, errors, skipped = counts
    if sum(skip_counts.values()) != skipped:
        raise ValueError(
            "TCK skip classification mismatch: "
            f"classified {sum(skip_counts.values())}, JUnit reports {skipped}"
        )
    status = "PASS" if exit_code == 0 and failures == 0 and errors == 0 else "FAIL"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# A2A Bridge Phase 1 Compatibility Report",
                "",
                f"- Result: **{status}**",
                f"- `a2a-sdk`: `{A2A_SDK_VERSION}`",
                f"- `a2a-tck`: `{A2A_TCK_COMMIT}`",
                "- TCK harness correction: `CORE-SEND-003` is missing its declared",
                "  `ContentTypeNotSupportedError` expectation at the pinned revision;",
                "  the runner applies and reverts the audited patch under `tools/patches/`.",
                "- Protocol binding: `JSONRPC`",
                "- Requirement level: `MUST`",
                f"- Tests: `{tests}`",
                f"- Failures: `{failures}`",
                f"- Errors: `{errors}`",
                f"- Skipped: `{skipped}`",
                f"- Exit code: `{exit_code}`",
                "- Skip review (fail-closed):",
                f"  - Transport-filtered: `{skip_counts['transport-filtered']}`",
                f"  - Push disabled: `{skip_counts['push-disabled']}`",
                f"  - Extended card disabled: `{skip_counts['extended-card-disabled']}`",
                f"  - Inverse streaming checks: `{skip_counts['inverse-streaming']}`",
                f"  - Required extension disabled: `{skip_counts['required-extension-disabled']}`",
                "  - Applicable JSON-RPC MUST skips: `0`",
                "",
                "## Command",
                "",
                "```text",
                " ".join(command),
                "```",
                "",
                "Raw JSON, HTML, and JUnit reports are retained locally under",
                "`.scratch/a2a-tck-phase1/reports/` and become CI artifacts in phase 4.",
                "",
                "This report proves the phase-1 SDK/route/test-fixture assembly only.",
                "It is not a conformance claim for the later production connector or bounded store.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tck-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    verify_tck_checkout(args.tck_dir)
    sut_url = f"http://127.0.0.1:{args.port}"
    env = os.environ.copy()
    env["A2A_TCK_SUT_BASE_URL"] = sut_url
    env.setdefault("TCK_STREAMING_TIMEOUT", "2.0")
    with corrected_tck_checkout(args.tck_dir):
        source_reports = args.tck_dir / "reports"
        shutil.rmtree(source_reports, ignore_errors=True)
        shutil.rmtree(DEFAULT_REPORT_DIR, ignore_errors=True)
        DEFAULT_SUMMARY.unlink(missing_ok=True)
        sut: subprocess.Popen[bytes] | None = None
        try:
            sut = subprocess.Popen(
                [
                    "uv",
                    "run",
                    "uvicorn",
                    "tests.tck_app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.port),
                    "--log-level",
                    "warning",
                ],
                cwd=BRIDGE_ROOT,
                env=env,
                start_new_session=True,
            )
            wait_for_card(sut_url, sut)
            command = build_tck_command(args.tck_dir, sut_url)
            result = subprocess.run(command, cwd=args.tck_dir, env=env, check=False)
            DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
            for name in (
                "compatibility.json",
                "compatibility.html",
                "tck_report.html",
                "junitreport.xml",
            ):
                shutil.copy2(source_reports / name, DEFAULT_REPORT_DIR / name)
            junit = DEFAULT_REPORT_DIR / "junitreport.xml"
            counts = junit_counts(junit)
            skip_counts = audit_junit_skips(junit)
            write_summary(
                DEFAULT_SUMMARY,
                command=command,
                counts=counts,
                exit_code=result.returncode,
                skip_counts=skip_counts,
            )
            return result.returncode
        finally:
            if sut is not None and sut.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(sut.pid, signal.SIGTERM)
                try:
                    sut.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(sut.pid, signal.SIGKILL)
                    sut.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
