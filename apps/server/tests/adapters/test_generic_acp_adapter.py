"""Contracts for declarative ACP provider integration."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from polynoia.adapters import acp as acp_runtime
from polynoia.adapters import pool
from polynoia.adapters.acp import (
    AcpContextInvalidatedError,
    AcpLaunchContext,
    AcpProvider,
    GenericAcpAdapter,
    GenericAcpSession,
)
from polynoia.adapters.acp_providers import (
    QWEN_CODE_PROVIDER,
    _ensure_qwen_acp_polynoia_always_load,
    _prepare_qwen_environment,
    build_registered_acp_adapters,
)
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
        self.authenticate_calls: list[dict[str, Any]] = []
        self.new_session_calls: list[dict[str, Any]] = []
        self.resume_session_calls: list[dict[str, Any]] = []
        self.load_session_calls: list[dict[str, Any]] = []
        self.config_calls: list[dict[str, Any]] = []

    async def initialize(self, **kwargs: Any) -> Any:
        self.initialize_calls.append(kwargs)
        return SimpleNamespace(protocol_version=acp_runtime.PROTOCOL_VERSION)

    async def new_session(self, **kwargs: Any) -> Any:
        self.new_session_calls.append(kwargs)
        return SimpleNamespace(session_id="demo-session")

    async def resume_session(self, **kwargs: Any) -> Any:
        self.resume_session_calls.append(kwargs)
        return SimpleNamespace()

    async def load_session(self, **kwargs: Any) -> Any:
        self.load_session_calls.append(kwargs)
        return SimpleNamespace()

    async def authenticate(self, **kwargs: Any) -> None:
        self.authenticate_calls.append(kwargs)

    async def set_config_option(self, **kwargs: Any) -> None:
        self.config_calls.append(kwargs)


def test_provider_record_builds_adapter_without_new_adapter_class() -> None:
    provider = _provider()

    adapters = build_registered_acp_adapters({"demo-acp": provider})

    assert set(adapters) == {"demo-acp"}
    assert type(adapters["demo-acp"]) is GenericAcpAdapter
    assert adapters["demo-acp"].provider is provider


@pytest.mark.asyncio
async def test_provider_without_native_skills_still_starts_empty_binding(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = GenericAcpAdapter(QWEN_CODE_PROVIDER)
    sandbox = SimpleNamespace(
        root=tmp_path,
        agent_id="contact",
        place_skill_packages=lambda *_args, **_kwargs: pytest.fail("native placement must not run"),
    )

    async def _create(_conv_id: str) -> Any:
        return sandbox

    monkeypatch.setattr(acp_runtime.Sandbox, "create", _create)
    session = await adapter.start_session(conv_id="conv", skills=[])

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
    assert isinstance(adapters["qwenCode"], GenericAcpAdapter)
    assert isinstance(adapters["deepseek"], GenericAcpAdapter)
    assert isinstance(adapters["claudeCode"], GenericAcpAdapter)
    assert isinstance(adapters["codex"], GenericAcpAdapter)


def test_qwen_provider_renders_model_and_endpoint_environment(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acp_runtime.shutil, "which", lambda *args, **kwargs: "/usr/local/bin/qwen")
    command = QWEN_CODE_PROVIDER.launch_command(
        cwd=str(tmp_path),
        env={"PATH": "", "POLYNOIA_MCP_CONFIG": "/managed/polynoia-mcp.json"},
        model="qwen3.7-plus",
    )
    assert command == (
        "/usr/local/bin/qwen",
        "--safe-mode",
        "--acp",
        "--approval-mode",
        "default",
        "--mcp-config",
        "/managed/polynoia-mcp.json",
        "--allowed-tools",
        "mcp__polynoia__read",
        "--exclude-tools",
        QWEN_CODE_PROVIDER.command[-1],
    )
    monkeypatch.setattr(acp_runtime.shutil, "which", lambda *args, **kwargs: None)

    env = {
        "POLYNOIA_LLM_API_KEY": "secret",
        "POLYNOIA_LLM_API_BASE_URL": "https://example.test/v1",
    }
    _prepare_qwen_environment(
        AcpLaunchContext(
            sandbox=SimpleNamespace(
                root=tmp_path,
                agent_runtime_home=lambda _adapter_id: tmp_path / "private-qwen-home",
            ),
            cwd=str(tmp_path),
            model="qwen3.7-plus",
        ),
        env,
    )
    assert env == {
        "OPENAI_API_KEY": "secret",
        "OPENAI_BASE_URL": "https://example.test/v1",
        "OPENAI_MODEL": "qwen3.7-plus",
        "HOME": str(tmp_path / "private-qwen-home"),
        "USERPROFILE": str(tmp_path / "private-qwen-home"),
        "QWEN_HOME": str(tmp_path / "private-qwen-home" / ".qwen"),
        "QWEN_CODE_SYSTEM_SETTINGS_PATH": str(
            tmp_path / "private-qwen-home" / ".qwen" / "polynoia-system-settings.json"
        ),
        "QWEN_CODE_TRUSTED_FOLDERS_PATH": str(
            tmp_path / "private-qwen-home" / ".qwen" / "trustedFolders.json"
        ),
    }
    system_settings = json.loads(
        (tmp_path / "private-qwen-home" / ".qwen" / "polynoia-system-settings.json").read_text()
    )
    assert system_settings["security"]["folderTrust"]["enabled"] is True
    assert system_settings["modelProviders"]["openai"][0]["baseUrl"] == ("https://example.test/v1")
    assert system_settings["features"]["tool_search"] is True
    assert system_settings["tools"]["toolSearch"]["enabled"] is True
    assert system_settings["permissions"] == {"allow": ["mcp__polynoia__read"]}
    assert "secret" not in json.dumps(system_settings)
    trust = json.loads(
        (tmp_path / "private-qwen-home" / ".qwen" / "trustedFolders.json").read_text()
    )
    assert trust[str(tmp_path.resolve())] == "TRUST_FOLDER"


def test_qwen_acp_compat_patch_is_narrow_and_idempotent(tmp_path: Any) -> None:
    package = tmp_path / "qwen-package"
    chunks = package / "chunks"
    chunks.mkdir(parents=True)
    executable = package / "cli-entry.js"
    executable.write_text("// entry")
    acp_bundle = chunks / "acpAgent-test.js"
    acp_bundle.write_text(
        """before
sessionMcpServers[stdioServer.name] = new MCPServerConfig(
          stdioServer.command,
          stdioServer.args,
          env
        );
after"""
    )
    search_bundle = chunks / "tool-search-test.js"
    search_bundle.write_text(
        """collectCandidates() {
    const registry = this.config.getToolRegistry();
    return registry.getAllTools().filter((t) => registry.isDeferredAndHidden(t.name));
  }
  async loadAndReturnSchemas(names, truncated = []) {
      if (!tool) {
        missing.push(requested);
        continue;
      }
      const isLoadable = registry.isDeferredAndHidden(canonical);
  }"""
    )
    provenance_bundle = chunks / "chunk-provenance.js"
    provenance_bundle.write_text(
        """static resolveToolProvenance(toolName, subagentMeta) {
    if (subagentMeta !== void 0) {
      return { provenance: "subagent" };
    }
    if (toolName.startsWith("mcp__")) {
      return { provenance: "mcp" };
    }
  }"""
    )

    assert _ensure_qwen_acp_polynoia_always_load(str(executable)) is True
    first = (acp_bundle.read_text(), search_bundle.read_text(), provenance_bundle.read_text())
    assert 'stdioServer.name === "polynoia"' in first[0]
    assert "sessionServerConfig.alwaysLoadTools = true" in first[0]
    assert 't.serverName === "polynoia"' in first[1]
    assert "!(tool instanceof DiscoveredMCPTool)" in first[1]
    assert 'toolName === "tool_search"' in first[2]
    assert 'serverId: "polynoia"' in first[2]
    assert _ensure_qwen_acp_polynoia_always_load(str(executable)) is True
    assert (
        acp_bundle.read_text(),
        search_bundle.read_text(),
        provenance_bundle.read_text(),
    ) == first


def test_direct_transport_keeps_dedicated_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pool, "_BASE_ADAPTERS", {})
    monkeypatch.setenv("POLYNOIA_HARNESS_TRANSPORT", "direct")

    adapters = pool._ensure_base_adapters()

    assert not isinstance(adapters["codex"], GenericAcpAdapter)
    assert not isinstance(adapters["claudeCode"], GenericAcpAdapter)
    assert isinstance(adapters["qwenCode"], GenericAcpAdapter)


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
    assert spawn_kwargs["cwd"] == str(tmp_path)
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


@pytest.mark.asyncio
async def test_generic_acp_resumes_durable_session_without_reinjecting_bootstrap(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound: list[tuple[str, bool]] = []

    async def on_bound(session_id: str, capabilities: dict[str, Any], resumed: bool) -> None:
        assert capabilities["sessionCapabilities"]["resume"] == {}
        bound.append((session_id, resumed))

    sandbox = SimpleNamespace(
        conv_id="conv-1",
        root=tmp_path,
        workspace_root=None,
        workspace_id=None,
        env_for_agent=lambda env: dict(env),
    )
    session = GenericAcpSession(
        provider=_provider(),
        sandbox=sandbox,
        conv_id="conv-1",
        cwd=str(tmp_path),
        model=None,
        system_prompt="RECOVERY SNAPSHOT",
        env={},
        agent_id="demo-acp",
        resume_session_id="durable-session",
        on_session_bound=on_bound,
    )
    connection = _Connection()
    process = _FakeProcess()
    capabilities = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "loadSession": True,
            "sessionCapabilities": {"resume": {}},
        }
    )

    async def initialize(**kwargs: Any) -> Any:
        connection.initialize_calls.append(kwargs)
        return SimpleNamespace(
            protocol_version=acp_runtime.PROTOCOL_VERSION,
            agent_capabilities=capabilities,
            auth_methods=[],
        )

    connection.initialize = initialize  # type: ignore[method-assign]

    @asynccontextmanager
    async def _spawn(*_args: Any, **_kwargs: Any):
        yield connection, process

    monkeypatch.setattr(acp_runtime, "spawn_agent_process", _spawn)
    monkeypatch.setattr(acp_runtime.shutil, "which", lambda *_a, **_k: "demo-acp")

    await session._ensure_subprocess()

    assert len(connection.resume_session_calls) == 1
    resume_call = connection.resume_session_calls[0]
    assert resume_call["session_id"] == "durable-session"
    assert resume_call["cwd"] == str(tmp_path)
    assert resume_call["mcp_servers"][0].name == "polynoia"
    assert connection.new_session_calls == []
    assert session._acp_session_id == "durable-session"
    assert session._sent_system is True
    assert bound == [("durable-session", True)]
    await session.close()


@pytest.mark.asyncio
async def test_generic_acp_aborts_before_prompt_when_pool_rejects_stale_binding(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_stale_binding(
        _session_id: str,
        _capabilities: dict[str, Any],
        _resumed: bool,
    ) -> bool:
        return False

    sandbox = SimpleNamespace(
        conv_id="conv-stale",
        root=tmp_path,
        workspace_root=None,
        workspace_id=None,
        env_for_agent=lambda env: dict(env),
    )
    session = GenericAcpSession(
        provider=_provider(),
        sandbox=sandbox,
        conv_id="conv-stale",
        cwd=str(tmp_path),
        model=None,
        system_prompt="STALE SNAPSHOT",
        env={},
        agent_id="demo-acp",
        on_session_bound=reject_stale_binding,
    )
    connection = _Connection()
    process = _FakeProcess()

    @asynccontextmanager
    async def _spawn(*_args: Any, **_kwargs: Any):
        yield connection, process

    monkeypatch.setattr(acp_runtime, "spawn_agent_process", _spawn)
    monkeypatch.setattr(acp_runtime.shutil, "which", lambda *_a, **_k: "demo-acp")

    with pytest.raises(AcpContextInvalidatedError):
        await session._ensure_subprocess()

    assert len(connection.new_session_calls) == 1
    assert session._acp_session_id is None
    assert session._connection is None


@pytest.mark.asyncio
async def test_codex_gateway_key_is_not_in_harness_process_environment(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polynoia.adapters.acp_providers import CODEX_ACP_PROVIDER

    runtime_home = tmp_path / "runtime-home"
    sandbox = SimpleNamespace(
        conv_id="conv-1",
        root=tmp_path,
        workspace_root=None,
        workspace_id=None,
        agent_runtime_home=lambda _adapter_id: runtime_home,
        env_for_agent=lambda extra: dict(extra),
    )
    session = GenericAcpSession(
        provider=CODEX_ACP_PROVIDER,
        sandbox=sandbox,
        conv_id="conv-1",
        cwd=str(tmp_path),
        model=None,
        system_prompt=None,
        env={
            "OPENAI_API_KEY": "test-gateway-secret",
            "OPENAI_BASE_URL": "https://gateway.example/v1",
        },
        agent_id="codex",
    )
    connection = _Connection()
    process = _FakeProcess()
    spawn_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    @asynccontextmanager
    async def _spawn(*args: Any, **kwargs: Any):
        spawn_calls.append((args, kwargs))
        yield connection, process

    monkeypatch.setattr(acp_runtime, "spawn_agent_process", _spawn)
    monkeypatch.setattr(acp_runtime.shutil, "which", lambda *args, **kwargs: "codex-acp")

    await session._ensure_subprocess()

    spawned_env = spawn_calls[0][1]["env"]
    assert "OPENAI_API_KEY" not in spawned_env
    assert connection.authenticate_calls == [
        {
            "method_id": "gateway",
            "gateway": {
                "baseUrl": "https://gateway.example/v1",
                "headers": {"Authorization": "Bearer test-gateway-secret"},
                "providerName": "Polynoia contact endpoint",
            },
        }
    ]

    await session.close()


def test_direct_proxy_policy_is_applied_after_sandbox_env(tmp_path: Any) -> None:
    sandbox = SimpleNamespace(
        conv_id="conv-1",
        root=tmp_path,
        workspace_root=None,
        workspace_id=None,
        env_for_agent=lambda extra: {"HTTPS_PROXY": "http://host-proxy:8080", **extra},
    )
    session = GenericAcpSession(
        provider=_provider(),
        sandbox=sandbox,
        conv_id="conv-1",
        cwd=str(tmp_path),
        model=None,
        system_prompt=None,
        env={},
        agent_id="demo-acp",
        proxy_kind="direct",
    )

    assert "HTTPS_PROXY" not in session._prepare_subprocess_env()
