"""Built-in ACP provider declarations.

Adding a standards-compliant ACP runtime should normally require one
``AcpProvider`` record here, plus product-facing onboarding/template metadata.
Only providers with real launch-time quirks need a small environment preparer.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path

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
    "skill": "deny",
    "lsp": "deny",
    "todoread": "deny",
    "todowrite": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "codesearch": "deny",
}


def _opencode_config_content(model: str | None) -> str:
    config: dict[str, object] = {
        "permission": {
            **_OPENCODE_BUILTIN_PERMISSION_DENY,
            "polynoia_*": "allow",
        },
    }
    if model:
        config["model"] = model
    return json.dumps(config)


def _write_opencode_config(path: Path, model: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_opencode_config_content(model), encoding="utf-8")


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

    env["XDG_DATA_HOME"] = _polynoia_opencode_data_home()
    config_path = context.sandbox.root / ".polynoia" / "opencode-config.json"
    _write_opencode_config(config_path, context.model)
    env["OPENCODE_CONFIG"] = str(config_path)
    env["OPENCODE_CONFIG_CONTENT"] = _opencode_config_content(context.model)
    # Do not enable OPENCODE_ACP_NEXT: OpenCode 1.15.x does not implement
    # prompt/cancel on that path. The default ACP v1 path streams correctly.


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


ACP_PROVIDERS: dict[str, AcpProvider] = {
    OPENCODE_PROVIDER.meta.agent_id: OPENCODE_PROVIDER,
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
