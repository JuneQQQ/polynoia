"""Contracts for declarative ACP provider integration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from polynoia.adapters import acp as acp_runtime
from polynoia.adapters import pool
from polynoia.adapters.acp import (
    AcpLaunchContext,
    AcpProvider,
    GenericAcpAdapter,
    GenericAcpSession,
)
from polynoia.adapters.acp_providers import build_registered_acp_adapters
from polynoia.adapters.base import AdapterCapabilities, AdapterMeta


def _provider(**kwargs: Any) -> AcpProvider:
    return AcpProvider(
        meta=AdapterMeta(
            agent_id="demo-acp",
            cli_command="demo-acp",
            auth_kinds=["cli-login"],
            base_model="demo/default",
            capabilities=AdapterCapabilities(mcp=True),
        ),
        command=("demo-acp", "serve", "--cwd", "{cwd}"),
        **kwargs,
    )


class _ImmediateEof:
    async def readline(self) -> bytes:
        return b""


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stderr = _ImmediateEof()


class _Connection:
    def __init__(self) -> None:
        self.initialize_calls: list[dict[str, Any]] = []
        self.new_session_calls: list[dict[str, Any]] = []
        self.config_calls: list[dict[str, Any]] = []

    async def initialize(self, **kwargs: Any) -> Any:
        self.initialize_calls.append(kwargs)
        return SimpleNamespace(protocol_version=acp_runtime.PROTOCOL_VERSION)

    async def new_session(self, **kwargs: Any) -> Any:
        self.new_session_calls.append(kwargs)
        return SimpleNamespace(session_id="demo-session")

    async def set_config_option(self, **kwargs: Any) -> None:
        self.config_calls.append(kwargs)


def test_provider_record_builds_adapter_without_new_adapter_class() -> None:
    provider = _provider()

    adapters = build_registered_acp_adapters({"demo-acp": provider})

    assert set(adapters) == {"demo-acp"}
    assert type(adapters["demo-acp"]) is GenericAcpAdapter
    assert adapters["demo-acp"].provider is provider


@pytest.mark.asyncio
async def test_generic_provider_without_native_skill_layout_starts(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acp_runtime.settings, "sandbox_root", tmp_path)

    session = await GenericAcpAdapter(_provider()).start_session(
        conv_id="generic-no-native-skills",
        skills=[],
    )

    assert session._skills == []


def test_provider_registry_rejects_mismatched_key() -> None:
    with pytest.raises(ValueError, match="does not match"):
        build_registered_acp_adapters({"wrong-id": _provider()})


@pytest.mark.asyncio
async def test_generic_detect_uses_provider_version_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = SimpleNamespace(
        returncode=0,
        communicate=lambda: None,
    )

    async def _communicate() -> tuple[bytes, bytes]:
        return b"demo-acp 2.4.1\n", b""

    async def _create_process(*args: Any, **kwargs: Any) -> Any:
        return process

    process.communicate = _communicate
    monkeypatch.setattr(acp_runtime.shutil, "which", lambda *args, **kwargs: "demo-acp")
    monkeypatch.setattr(
        acp_runtime.asyncio,
        "create_subprocess_exec",
        _create_process,
    )

    detected, version = await GenericAcpAdapter(_provider()).detect()

    assert detected is True
    assert version == "2.4.1"


def test_pool_builds_opencode_from_acp_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pool, "_BASE_ADAPTERS", {})

    adapters = pool._ensure_base_adapters()

    assert isinstance(adapters["opencoder"], GenericAcpAdapter)
    assert adapters["opencoder"].provider.meta.agent_id == "opencoder"


def test_acp_provider_cannot_override_dedicated_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pool, "_BASE_ADAPTERS", {})
    monkeypatch.setattr(
        pool,
        "build_registered_acp_adapters",
        lambda: {"codex": GenericAcpAdapter(_provider())},
    )

    with pytest.raises(ValueError, match="conflicts with dedicated adapter: codex"):
        pool._ensure_base_adapters()


@pytest.mark.asyncio
async def test_generic_session_applies_declarative_launch_and_model_config(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared: list[AcpLaunchContext] = []

    def _prepare(context: AcpLaunchContext, env: dict[str, str]) -> None:
        prepared.append(context)
        env["DEMO_MODEL"] = context.model or ""

    provider = _provider(
        prepare_environment=_prepare,
        model_config_option="model",
    )
    sandbox = SimpleNamespace(
        conv_id="conv-1",
        root=tmp_path,
        workspace_root=None,
        workspace_id=None,
        env_for_agent=lambda env: dict(env),
    )
    session = GenericAcpSession(
        provider=provider,
        sandbox=sandbox,
        conv_id="conv-1",
        cwd=str(tmp_path),
        model="demo/large",
        system_prompt=None,
        env={},
        agent_id="demo-acp",
    )
    connection = _Connection()
    process = _FakeProcess()
    spawn_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    @asynccontextmanager
    async def _spawn(*args: Any, **kwargs: Any):
        spawn_calls.append((args, kwargs))
        yield connection, process

    monkeypatch.setattr(acp_runtime, "spawn_agent_process", _spawn)
    monkeypatch.setattr(
        acp_runtime.shutil,
        "which",
        lambda *args, **kwargs: "C:/tools/demo-acp.exe",
    )

    await session._ensure_subprocess()

    command_args, spawn_kwargs = spawn_calls[0]
    assert command_args[1:] == (
        "C:/tools/demo-acp.exe",
        "serve",
        "--cwd",
        str(tmp_path),
    )
    assert spawn_kwargs["env"]["DEMO_MODEL"] == "demo/large"
    assert prepared[0].sandbox is sandbox
    assert connection.new_session_calls[0]["cwd"] == str(tmp_path)
    assert connection.new_session_calls[0]["mcp_servers"][0].name == "polynoia"
    assert connection.config_calls == [
        {
            "session_id": "demo-session",
            "config_id": "model",
            "value": "demo/large",
        }
    ]

    await session.close()
