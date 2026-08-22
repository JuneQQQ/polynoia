"""Built-in ACP provider declarations.

Adding a standards-compliant ACP runtime should normally require one
``AcpProvider`` record here, plus product-facing onboarding/template metadata.
Only providers with real launch-time quirks need a small environment preparer.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from polynoia.adapters.acp import AcpLaunchContext, AcpProvider, GenericAcpAdapter
from polynoia.adapters.base import AdapterCapabilities, AdapterMeta
from polynoia.credentials import credential_source_home
from polynoia.settings import settings

_OPENCODE_BUILTIN_PERMISSION_DENY: dict[str, str] = {
    "read": "deny",
    "edit": "deny",
    "glob": "deny",
    "grep": "deny",
    "list": "deny",
    "bash": "deny",
    "task": "deny",
    "lsp": "deny",
    "todoread": "deny",
    "todowrite": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "codesearch": "deny",
}

# Qwen 0.21.14 forwards OPENAI_API_KEY into native tool subprocesses. Disable
# its complete built-in tool surface and expose only the Polynoia MCP server,
# whose explicitly constructed environment contains no model credential.
_QWEN_COMPUTER_USE_TOOLS = tuple(
    f"computer_use__{name}"
    for name in (
        "bring_to_front",
        "check_for_update",
        "check_permissions",
        "click",
        "double_click",
        "drag",
        "end_session",
        "get_accessibility_tree",
        "get_agent_cursor_state",
        "get_config",
        "get_cursor_position",
        "get_recording_state",
        "get_screen_size",
        "get_window_state",
        "hotkey",
        "kill_app",
        "launch_app",
        "list_apps",
        "list_windows",
        "move_cursor",
        "page",
        "press_key",
        "replay_trajectory",
        "right_click",
        "scroll",
        "set_agent_cursor_enabled",
        "set_agent_cursor_motion",
        "set_agent_cursor_style",
        "set_config",
        "set_value",
        "start_recording",
        "start_session",
        "stop_recording",
        "type_text",
        "zoom",
    )
)

_QWEN_NATIVE_TOOLS = (
    "edit",
    "write_file",
    "read_file",
    "zoom_image",
    "grep_search",
    "glob",
    "run_shell_command",
    "todo_write",
    "save_memory",
    "agent",
    "skill",
    "exit_plan_mode",
    "enter_plan_mode",
    "web_fetch",
    "web_search",
    "image_gen",
    "list_directory",
    "lsp",
    "ask_user_question",
    "cron_create",
    "cron_list",
    "cron_delete",
    "loop_wakeup",
    "create_sub_session",
    "list_agents",
    "task_stop",
    "task_create",
    "task_update",
    "task_list",
    "team_create",
    "team_delete",
    "team_plan_approval",
    "send_message",
    "structured_output",
    "monitor",
    "notebook_edit",
    "read_mcp_resource",
    "enter_worktree",
    "exit_worktree",
    "workflow",
    "artifact",
    "record_artifact",
    "get_goal",
    "update_goal",
    "display_image",
    *_QWEN_COMPUTER_USE_TOOLS,
)

_QWEN_ACP_ALWAYS_LOAD_MARKER = "/* polynoia: always load controlled ACP MCP */"
_QWEN_ACP_STDIO_SERVER = """sessionMcpServers[stdioServer.name] = new MCPServerConfig(
          stdioServer.command,
          stdioServer.args,
          env
        );"""
_QWEN_ACP_STDIO_SERVER_PATCH = """const sessionServerConfig = new MCPServerConfig(
          stdioServer.command,
          stdioServer.args,
          env
        );
        if (stdioServer.name === \"polynoia\") {
          sessionServerConfig.alwaysLoadTools = true;
        }
        sessionMcpServers[stdioServer.name] = sessionServerConfig;
        /* polynoia: always load controlled ACP MCP */"""
_QWEN_TOOL_SEARCH_FILTER_MARKER = "/* polynoia: controlled schema candidates only */"
_QWEN_TOOL_SEARCH_CANDIDATES = (
    "return registry.getAllTools().filter((t) => registry.isDeferredAndHidden(t.name));"
)
_QWEN_TOOL_SEARCH_CANDIDATES_PATCH = """return registry.getAllTools().filter(
      (t) => registry.isDeferredAndHidden(t.name) && t instanceof DiscoveredMCPTool && t.serverName === "polynoia"
    );
    /* polynoia: controlled schema candidates only */"""
_QWEN_TOOL_SEARCH_LOADED = """if (!tool) {
        missing.push(requested);
        continue;
      }
      const isLoadable = registry.isDeferredAndHidden(canonical);"""
_QWEN_TOOL_SEARCH_LOADED_PATCH = """if (!tool) {
        missing.push(requested);
        continue;
      }
      if (!(tool instanceof DiscoveredMCPTool) || tool.serverName !== "polynoia") {
        missing.push(requested);
        continue;
      }
      const isLoadable = registry.isDeferredAndHidden(canonical);"""
_QWEN_CONTROL_PROVENANCE_MARKER = "/* polynoia: schema search control plane */"
_QWEN_CONTROL_PROVENANCE = """if (subagentMeta !== void 0) {
      return { provenance: "subagent" };
    }
    if (toolName.startsWith("mcp__")) {"""
_QWEN_CONTROL_PROVENANCE_PATCH = """if (subagentMeta !== void 0) {
      return { provenance: "subagent" };
    }
    if (toolName === "tool_search") {
      return { provenance: "mcp", serverId: "polynoia" };
    }
    /* polynoia: schema search control plane */
    if (toolName.startsWith("mcp__")) {"""


def _patch_qwen_chunk(
    candidate: Path,
    *,
    marker: str,
    replacements: tuple[tuple[str, str], ...],
) -> bool:
    with contextlib.suppress(OSError):
        source = candidate.read_text(encoding="utf-8")
        if marker in source:
            return True
        if any(source.count(before) != 1 for before, _ in replacements):
            return False
        patched = source
        for before, after in replacements:
            patched = patched.replace(before, after, 1)
        temporary = candidate.with_name(f".{candidate.name}.polynoia-{os.getpid()}.tmp")
        try:
            temporary.write_text(patched, encoding="utf-8")
            temporary.chmod(candidate.stat().st_mode)
            os.replace(temporary, candidate)
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink()
        return True
    return False


def _ensure_qwen_acp_polynoia_always_load(executable: str | None) -> bool:
    """Install Qwen 0.21.x's Polynoia-only schema-discovery control plane.

    Standard ACP ``McpServerStdio`` cannot express Qwen's private
    ``alwaysLoadTools`` flag. Its ToolSearch is therefore retained only as a
    schema control plane: both keyword and explicit selection are filtered to
    ``DiscoveredMCPTool(serverName=polynoia)``, and emitted provenance is
    stamped as the Polynoia surface. Exact-source matching makes an upstream
    layout change fail safely instead of weakening the execution boundary.
    """

    if not executable:
        return False
    package_root = Path(executable).resolve().parent
    chunks = package_root / "chunks"
    if not chunks.is_dir():
        return False
    acp_ok = any(
        _patch_qwen_chunk(
            candidate,
            marker=_QWEN_ACP_ALWAYS_LOAD_MARKER,
            replacements=((_QWEN_ACP_STDIO_SERVER, _QWEN_ACP_STDIO_SERVER_PATCH),),
        )
        for candidate in sorted(chunks.glob("acpAgent-*.js"))
    )
    search_ok = any(
        _patch_qwen_chunk(
            candidate,
            marker=_QWEN_TOOL_SEARCH_FILTER_MARKER,
            replacements=(
                (_QWEN_TOOL_SEARCH_CANDIDATES, _QWEN_TOOL_SEARCH_CANDIDATES_PATCH),
                (_QWEN_TOOL_SEARCH_LOADED, _QWEN_TOOL_SEARCH_LOADED_PATCH),
            ),
        )
        for candidate in sorted(chunks.glob("tool-search-*.js"))
    )
    provenance_ok = any(
        _patch_qwen_chunk(
            candidate,
            marker=_QWEN_CONTROL_PROVENANCE_MARKER,
            replacements=((_QWEN_CONTROL_PROVENANCE, _QWEN_CONTROL_PROVENANCE_PATCH),),
        )
        for candidate in sorted(chunks.glob("chunk-*.js"))
    )
    return acp_ok and search_ok and provenance_ok


def _opencode_config_content(
    model: str | None,
    skills: Iterable[str] | Mapping[str, str] = (),
    endpoint_env: Mapping[str, str] | None = None,
) -> str:
    # The two-argument mapping form is kept for compatibility with callers
    # that configured OpenCode endpoints before native Skills were introduced.
    if isinstance(skills, Mapping) and endpoint_env is None:
        endpoint_env = skills
        skills = ()
    config: dict[str, object] = {
        "permission": {
            **_OPENCODE_BUILTIN_PERMISSION_DENY,
            # Project-local skills are discoverable too. Expose only packages
            # explicitly bound to this contact.
            "skill": {"*": "deny", **{name: "allow" for name in skills}},
            "polynoia_*": "allow",
        },
    }
    if model:
        config["model"] = model
    api_key = (endpoint_env or {}).get("POLYNOIA_LLM_API_KEY")
    api_base_url = (endpoint_env or {}).get("POLYNOIA_LLM_API_BASE_URL")
    if api_key or api_base_url:
        provider_id = model.split("/", 1)[0] if model and "/" in model else "openai"
        options: dict[str, str] = {}
        if api_key:
            options["apiKey"] = api_key
        if api_base_url:
            options["baseURL"] = api_base_url
        config["provider"] = {provider_id: {"options": options}}
    return json.dumps(config)


def _write_opencode_config(
    path: Path,
    model: str | None,
    skills: Iterable[str] = (),
    endpoint_env: Mapping[str, str] | None = None,
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _opencode_config_content(model, skills, endpoint_env)
    path.write_text(content, encoding="utf-8")
    return content


def _opencode_executable(env: dict[str, str]) -> str:
    path = shutil.which("opencode", path=env.get("PATH"))
    if path:
        return path
    raise FileNotFoundError(
        "OpenCode CLI 未找到。请确认已安装 opencode, 且所在目录在后端服务的 PATH 中。"
    )


def _polynoia_opencode_data_home() -> str:
    """Return Polynoia's OpenCode data directory, isolated from the user's."""

    target = settings.sandbox_root / "_opencode_home"
    data = target / "opencode"
    data.mkdir(parents=True, exist_ok=True)

    default_data_home = credential_source_home() / ".local" / "share"
    host = Path(os.environ.get("XDG_DATA_HOME", str(default_data_home))) / "opencode"
    host_auth = host / "auth.json"
    if host_auth.exists():
        with contextlib.suppress(Exception):
            shutil.copy2(host_auth, data / "auth.json")
    host_db = host / "opencode.db"
    if host_db.exists() and not (data / "opencode.db").exists():
        with contextlib.suppress(Exception):
            shutil.copy2(host_db, data / "opencode.db")
    return str(target)


def _prepare_opencode_environment(
    context: AcpLaunchContext,
    env: dict[str, str],
) -> None:
    """Preserve the OpenCode-specific isolation, model and tool policy."""

    # Keep OpenCode's native Skill directory contact-scoped while retaining
    # explicit config/data paths for its isolated credentials and session DB.
    runtime_home = getattr(context.sandbox, "agent_runtime_home", None)
    skill_home = runtime_home("opencoder") if callable(runtime_home) else context.sandbox.root
    env["HOME"] = str(skill_home)
    env["USERPROFILE"] = str(skill_home)
    env["XDG_DATA_HOME"] = _polynoia_opencode_data_home()
    config_path = context.sandbox.root / ".polynoia" / "opencode-config.json"
    config_content = _write_opencode_config(config_path, context.model, context.skills, env)
    env["OPENCODE_CONFIG"] = str(config_path)
    env["OPENCODE_CONFIG_CONTENT"] = config_content
    # Do not enable OPENCODE_ACP_NEXT: OpenCode 1.15.x does not implement
    # prompt/cancel on that path. The default ACP v1 path streams correctly.


def _prepare_qwen_environment(
    context: AcpLaunchContext,
    env: dict[str, str],
) -> None:
    """Expose a contact endpoint through Qwen Code's OpenAI-compatible env."""

    qwen_executable = shutil.which("qwen", path=env.get("PATH"))
    if qwen_executable:
        package_json = Path(qwen_executable).resolve().parent / "package.json"
        qwen_version: str | None = None
        with contextlib.suppress(json.JSONDecodeError, OSError):
            package_data = json.loads(package_json.read_text(encoding="utf-8"))
            if isinstance(package_data, dict) and isinstance(package_data.get("version"), str):
                qwen_version = package_data["version"]
        if not _ensure_qwen_acp_polynoia_always_load(qwen_executable):
            raise RuntimeError(
                "Qwen ACP bundle does not match the Polynoia-only schema control-plane patch"
                f" (detected {qwen_version or 'unknown'})"
            )

    api_key = env.pop("POLYNOIA_LLM_API_KEY", None)
    api_base_url = env.pop("POLYNOIA_LLM_API_BASE_URL", None)
    if api_key:
        env["OPENAI_API_KEY"] = api_key
    if api_base_url:
        env["OPENAI_BASE_URL"] = api_base_url
    if context.model:
        env["OPENAI_MODEL"] = context.model
    runtime_home = getattr(context.sandbox, "agent_runtime_home", None)
    qwen_runtime_root = (
        runtime_home("qwenCode")
        if callable(runtime_home)
        else context.sandbox.root / ".polynoia" / "agent-homes" / "qwenCode"
    )
    qwen_home = Path(qwen_runtime_root) / ".qwen"
    qwen_home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(qwen_runtime_root)
    env["USERPROFILE"] = str(qwen_runtime_root)
    env["QWEN_HOME"] = str(qwen_home)

    # QWEN_HOME is contact-scoped. Seed only the small auth/settings allowlist
    # from the shared credential snapshot (or host in direct-creds mode). Never
    # let one contact overwrite another while processes start concurrently.
    credential_root = Path(
        getattr(
            context.sandbox,
            "credentials_home",
            context.sandbox.root / ".polynoia" / "credentials",
        )
    )
    sources = (credential_root / ".qwen", credential_source_home() / ".qwen")
    for filename in ("oauth_creds.json",):
        destination = qwen_home / filename
        if destination.exists():
            continue
        source = next(
            (root / filename for root in sources if (root / filename).is_file()),
            None,
        )
        if source is not None:
            shutil.copy2(source, destination)
    # Do not copy host settings wholesale: user MCP/hooks/extensions/env would
    # inherit the contact key. Preserve only the OAuth selector; the token is
    # the separately allowlisted oauth_creds.json above.
    selected_auth: str | None = None
    for source_root in sources:
        source_settings = source_root / "settings.json"
        if not source_settings.is_file():
            continue
        with contextlib.suppress(json.JSONDecodeError, OSError):
            loaded = json.loads(source_settings.read_text(encoding="utf-8"))
            candidate = (
                loaded.get("security", {}).get("auth", {}).get("selectedType")
                if isinstance(loaded, dict)
                else None
            )
            if candidate == "qwen-oauth":
                selected_auth = candidate
                break
    user_content: dict[str, Any] = {"$version": 4}
    if selected_auth:
        user_content["security"] = {"auth": {"selectedType": selected_auth}}
    user_settings = qwen_home / "settings.json"
    user_settings.write_text(
        json.dumps(user_content, ensure_ascii=False),
        encoding="utf-8",
    )

    # System settings merge after workspace settings in Qwen 0.21.14. Pin the
    # endpoint there as defense in depth; --safe-mode suppresses workspace/user
    # hooks, extensions, MCP and other customizations entirely.
    managed_settings: dict[str, Any] = {
        "$version": 4,
        "security": {"folderTrust": {"enabled": True}},
        "mcpServers": {},
        # Defense in depth for non-safe-mode launches. The authoritative
        # read-only allowance is also passed via --allowed-tools below because
        # Qwen 0.21.x intentionally ignores settings permissions in safe mode.
        "permissions": {"allow": ["mcp__polynoia__read"]},
        # This runtime's ToolSearch implementation is compatibility-patched to
        # reveal only Polynoia MCP schemas; it cannot expose or execute a
        # Harness-native candidate.
        "tools": {"toolSearch": {"enabled": True}},
        "features": {
            "browser_use": False,
            "browser_use_external": False,
            "computer_use": False,
            "image_generation": False,
            "tool_search": True,
        },
    }
    if api_key and api_base_url:
        managed_settings["security"]["auth"] = {"selectedType": "openai"}
    if context.model:
        managed_settings["model"] = {"name": context.model}
    if context.model and api_base_url:
        managed_settings["modelProviders"] = {
            "openai": [
                {
                    "id": context.model,
                    "name": context.model,
                    "envKey": "OPENAI_API_KEY",
                    "baseUrl": api_base_url,
                    "generationConfig": {
                        "timeout": 180000,
                        "maxRetries": 2,
                        "contextWindowSize": 1000000,
                        "samplingParams": {"max_tokens": 4096},
                        "extra_body": {"enable_thinking": False},
                    },
                }
            ]
        }
    system_settings = qwen_home / "polynoia-system-settings.json"
    system_settings.write_text(
        json.dumps(managed_settings, ensure_ascii=False),
        encoding="utf-8",
    )
    env["QWEN_CODE_SYSTEM_SETTINGS_PATH"] = str(system_settings)
    trust_settings = qwen_home / "trustedFolders.json"
    trust_settings.write_text(
        json.dumps({str(Path(context.cwd).resolve()): "TRUST_FOLDER"}),
        encoding="utf-8",
    )
    env["QWEN_CODE_TRUSTED_FOLDERS_PATH"] = str(trust_settings)


def _prepare_claude_environment(
    context: AcpLaunchContext,
    env: dict[str, str],
) -> None:
    """Keep Claude inexpensive while preserving the sandbox credential HOME."""

    del context
    env.setdefault("MAX_THINKING_TOKENS", "0")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        env.setdefault("IS_SANDBOX", "1")


def _prepare_codex_environment(
    context: AcpLaunchContext,
    env: dict[str, str],
) -> None:
    """Build a contact-isolated, Polynoia-MCP-only Codex runtime."""

    runtime_home = getattr(context.sandbox, "agent_runtime_home", None)
    if callable(runtime_home):
        contact_home = runtime_home("codex")
        contact_home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(contact_home)
        env["USERPROFILE"] = str(contact_home)

        # Do not let host/user config re-introduce MCP servers, plugins or
        # project instructions.  Keep only the small auth/model cache files in
        # a contact-scoped CODEX_HOME; the effective runtime policy is supplied
        # below through CODEX_CONFIG.
        source_codex_home = Path(env.get("CODEX_HOME") or "")
        managed_codex_home = contact_home / ".codex"
        managed_codex_home.mkdir(parents=True, exist_ok=True)
        if source_codex_home.is_dir() and source_codex_home != managed_codex_home:
            for name in (
                "auth.json",
                ".codex-global-state.json",
                "models_cache.json",
                "version.json",
            ):
                source = source_codex_home / name
                if source.is_file():
                    shutil.copy2(source, managed_codex_home / name)
        (managed_codex_home / "config.toml").write_text(
            "# Managed by Polynoia; per-session policy arrives through CODEX_CONFIG.\n",
            encoding="utf-8",
        )
        env["CODEX_HOME"] = str(managed_codex_home)

    inherited: dict[str, object] = {}
    with contextlib.suppress(json.JSONDecodeError):
        value = json.loads(env.get("CODEX_CONFIG", "{}"))
        if isinstance(value, dict):
            inherited = value
    inherited["model_reasoning_summary"] = "auto"
    inherited["model_reasoning_effort"] = "low"
    if context.model:
        inherited["model"] = context.model
    inherited["approval_policy"] = "never"
    inherited["sandbox_mode"] = "read-only"
    inherited["web_search"] = "disabled"
    inherited["mcp_servers"] = {}
    inherited["apps"] = {"_default": {"enabled": False}}
    inherited["plugins"] = {}
    inherited["skills"] = {"config": []}
    inherited["tools"] = {"view_image": False, "web_search": False}
    features = inherited.get("features")
    if not isinstance(features, dict):
        features = {}
    for feature in (
        "apps",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "code_mode",
        "code_mode_host",
        "computer_use",
        "goals",
        "hooks",
        "image_generation",
        "in_app_browser",
        "memories",
        "multi_agent",
        "multi_agent_v2",
        "plugins",
        "remote_plugin",
        "request_permissions_tool",
        "shell_tool",
        "skill_search",
        "tool_suggest",
        "unified_exec",
        "view_image",
    ):
        features[feature] = False
    inherited["features"] = features
    env["CODEX_CONFIG"] = json.dumps(inherited)
    # codex-acp applies this preset directly to every turn, overriding the
    # sandbox/approval values from Codex config.  Assignment (not setdefault)
    # prevents caller environment from reopening workspace-write mode.
    env["INITIAL_AGENT_MODE"] = "read-only"
    # codex-acp 1.6 logs raw app-server JSON without secret redaction. Never
    # enable APP_SERVER_LOGS in ordinary contact sessions.
    env.pop("APP_SERVER_LOGS", None)


def _codex_authentication_request(
    env: dict[str, str],
) -> tuple[str, dict[str, Any]] | None:
    """Build a process-memory-only gateway request for codex-acp."""

    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    return (
        "gateway",
        {
            "gateway": {
                "baseUrl": base_url,
                "headers": {"Authorization": f"Bearer {api_key}"},
                "providerName": "Polynoia contact endpoint",
            }
        },
    )


_DSH_MODEL_RE = re.compile(r"^[A-Za-z0-9._/:+\-]{1,160}$")


def _prepare_dsh_environment(
    context: AcpLaunchContext,
    env: dict[str, str],
) -> None:
    """Materialize an official DeepSeek Harness ACP composition per sandbox."""

    model = context.model or "deepseek-v4-flash-0731"
    if not _DSH_MODEL_RE.fullmatch(model):
        raise ValueError(f"invalid DeepSeek Harness model id: {model!r}")
    runtime_home = getattr(context.sandbox, "agent_runtime_home", None)
    contact_home = (
        Path(runtime_home("deepseek"))
        if callable(runtime_home)
        else context.sandbox.root / ".polynoia" / "agent-homes" / "deepseek"
    )
    contact_home.mkdir(parents=True, exist_ok=True)
    template_path = Path(__file__).with_name("dsh_acp.cordis.yml")
    config_path = contact_home / "dsh-acp.cordis.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = template_path.read_text(encoding="utf-8")
    config = config.replace("__POLYNOIA_DSH_MODEL__", model)
    config = config.replace(
        "__POLYNOIA_DSH_PERSISTENCE_ROOT__",
        json.dumps(str(contact_home / "sessions")),
    )
    config_path.write_text(config, encoding="utf-8")
    env["POLYNOIA_ACP_CONFIG"] = str(config_path)


OPENCODE_PROVIDER = AcpProvider(
    meta=AdapterMeta(
        agent_id="opencoder",
        cli_command="opencode",
        detected=False,
        auth_kinds=["cli-login", "api-key"],
        base_model="claude-opus-4-7",
        docs="https://opencode.ai",
        capabilities=AdapterCapabilities(
            streaming=True,
            tool_calling="native",
            permissions=False,
            hooks=[],
            multi_session=True,
            sub_agents=False,
            mcp=True,
            file_edit_formats=["search-replace", "whole"],
            custom_endpoint=False,
        ),
    ),
    command=("opencode", "acp", "--cwd", "{cwd}"),
    version_token_index=0,
    prepare_environment=_prepare_opencode_environment,
    # OpenCode may emit its final message chunk just after prompt response.
    trailing_flush_grace_s=0.2,
)


QWEN_CODE_PROVIDER = AcpProvider(
    meta=AdapterMeta(
        agent_id="qwenCode",
        cli_command="qwen",
        detected=False,
        auth_kinds=["cli-login", "api-key", "llm-endpoint"],
        base_model="qwen3.6-flash",
        docs="https://qwenlm.github.io/qwen-code-docs/",
        capabilities=AdapterCapabilities(
            streaming=True,
            tool_calling="native",
            permissions=True,
            hooks=[],
            multi_session=True,
            sub_agents=False,
            mcp=True,
            file_edit_formats=["search-replace", "whole"],
            custom_endpoint=True,
        ),
    ),
    # Qwen Code currently defaults to the classifier-driven `auto` approval
    # mode.  That can execute writes without ever issuing ACP
    # session/request_permission, bypassing Polynoia's explicit approval UI.
    command=(
        "qwen",
        "--safe-mode",
        "--acp",
        "--approval-mode",
        "default",
        "--mcp-config",
        "{mcp_config}",
        "--allowed-tools",
        "mcp__polynoia__read",
        "--exclude-tools",
        ",".join(_QWEN_NATIVE_TOOLS),
    ),
    version_token_index=-1,
    prepare_environment=_prepare_qwen_environment,
    model_config_option="model",
    # Safe mode suppresses repository/user hooks, extensions and dynamic tools;
    # built-ins are excluded above. The ACP-injected Polynoia MCP is therefore
    # the only tool surface and its child environment contains no model key.
    pass_mcp_server=True,
    mcp_config_mode="qwen-cli-always-load",
    clear_mcp_parent_env=True,
    # CLI/config exclusion is defense in depth. Qwen has added dynamic native
    # tools between releases, so every non-Polynoia tool/sub-agent ACP frame is
    # also a fatal policy violation and destroys the runtime.
    tool_surface_policy="polynoia-mcp-only",
    # Qwen 0.21.x emits `qwen/notify/...` instead of ACP's required
    # `_qwen/notify/...`; the generic runtime routes that prefix directly to
    # ext_notification while retaining the original raw method and params.
    legacy_extension_notification_prefixes=("qwen/notify/",),
)


CLAUDE_CODE_PROVIDER = AcpProvider(
    meta=AdapterMeta(
        agent_id="claudeCode",
        cli_command="claude-agent-acp",
        detected=False,
        auth_kinds=["cli-login", "api-key", "llm-endpoint"],
        base_model="claude-haiku-4-5",
        docs="https://github.com/agentclientprotocol/claude-agent-acp",
        capabilities=AdapterCapabilities(
            streaming=True,
            tool_calling="native",
            permissions=True,
            hooks=[],
            multi_session=True,
            sub_agents=True,
            mcp=True,
            file_edit_formats=["search-replace", "whole"],
            custom_endpoint=True,
        ),
    ),
    command=("claude-agent-acp",),
    version_token_index=-1,
    prepare_environment=_prepare_claude_environment,
    model_config_option="model",
    client_capabilities_meta={
        "subagent-transcript": True,
        "terminal_output": True,
    },
    new_session_meta={
        "disableBuiltInTools": True,
        "claudeCode": {
            "options": {
                "tools": [],
                "settingSources": [],
                "strictMcpConfig": True,
                "permissionMode": "bypassPermissions",
            }
        },
    },
    system_prompt_mode="claude-meta",
)


CODEX_ACP_PROVIDER = AcpProvider(
    meta=AdapterMeta(
        agent_id="codex",
        cli_command="codex-acp",
        detected=False,
        auth_kinds=["cli-login", "api-key", "llm-endpoint"],
        base_model="gpt-5.4-mini",
        docs="https://github.com/agentclientprotocol/codex-acp",
        capabilities=AdapterCapabilities(
            streaming=True,
            tool_calling="native",
            permissions=False,
            hooks=[],
            multi_session=True,
            sub_agents=False,
            mcp=True,
            file_edit_formats=["apply-patch", "whole"],
            custom_endpoint=True,
        ),
    ),
    command=("codex-acp",),
    version_token_index=-1,
    prepare_environment=_prepare_codex_environment,
    authentication_builder=_codex_authentication_request,
    model_config_option="model",
    client_capabilities_meta={
        "subagent-transcript": False,
        "terminal_output": False,
    },
    tool_surface_policy="polynoia-mcp-only",
)


DEEPSEEK_HARNESS_PROVIDER = AcpProvider(
    meta=AdapterMeta(
        agent_id="deepseek",
        cli_command="dsh-acp-demo",
        detected=False,
        auth_kinds=["api-key", "llm-endpoint"],
        base_model="deepseek-v4-flash-0731",
        docs="https://github.com/deepseek-ai/deepseek-harness",
        capabilities=AdapterCapabilities(
            streaming=True,
            tool_calling="native",
            permissions=True,
            hooks=[],
            multi_session=False,
            sub_agents=False,
            mcp=True,
            file_edit_formats=["search-replace", "whole"],
            custom_endpoint=True,
        ),
    ),
    command=("dsh-acp-demo", "--config", "{config}"),
    prepare_environment=_prepare_dsh_environment,
    pass_mcp_server=True,
    tool_surface_policy="polynoia-mcp-only",
    detect_by_presence=True,
)


ACP_PROVIDERS: dict[str, AcpProvider] = {
    CLAUDE_CODE_PROVIDER.meta.agent_id: CLAUDE_CODE_PROVIDER,
    CODEX_ACP_PROVIDER.meta.agent_id: CODEX_ACP_PROVIDER,
    OPENCODE_PROVIDER.meta.agent_id: OPENCODE_PROVIDER,
    QWEN_CODE_PROVIDER.meta.agent_id: QWEN_CODE_PROVIDER,
    DEEPSEEK_HARNESS_PROVIDER.meta.agent_id: DEEPSEEK_HARNESS_PROVIDER,
}


def build_registered_acp_adapters(
    providers: dict[str, AcpProvider] | None = None,
) -> dict[str, GenericAcpAdapter]:
    """Build adapter factories from provider records, rejecting bad keys."""

    selected = ACP_PROVIDERS if providers is None else providers
    adapters: dict[str, GenericAcpAdapter] = {}
    for adapter_id, provider in selected.items():
        if adapter_id != provider.meta.agent_id:
            raise ValueError(
                f"ACP provider key {adapter_id!r} does not match "
                f"meta.agent_id {provider.meta.agent_id!r}"
            )
        adapters[adapter_id] = GenericAcpAdapter(provider)
    return adapters
