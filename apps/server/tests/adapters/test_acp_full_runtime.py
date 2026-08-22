"""Full-duplex ACP behavior shared by every Harness provider."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from acp.client.connection import ClientSideConnection
from acp.schema import PermissionOption, ToolCallStart, ToolCallUpdate

from polynoia.adapters.acp import (
    GenericAcpSession,
    _AcpClient,
    _mcp_only_policy_violation,
    translate_acp_stream_to_pap,
)
from polynoia.adapters.acp_providers import (
    ACP_PROVIDERS,
    CODEX_ACP_PROVIDER,
    QWEN_CODE_PROVIDER,
    _codex_authentication_request,
    _prepare_codex_environment,
    _prepare_dsh_environment,
)
from polynoia.adapters.base import (
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
)
from polynoia.transport.adapter_to_chunk import adapter_events_to_chunks


async def _items(*items: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    for item in items:
        yield item


def _session(
    tmp_path: Any,
    *,
    provider=QWEN_CODE_PROVIDER,
    system_prompt: str | None = None,
) -> GenericAcpSession:
    sandbox = SimpleNamespace(
        conv_id="conv-1",
        root=tmp_path,
        workspace_root=None,
        workspace_id=None,
        env_for_agent=lambda env: dict(env),
    )
    return GenericAcpSession(
        provider=provider,
        sandbox=sandbox,
        conv_id="conv-1",
        cwd=str(tmp_path),
        model=None,
        system_prompt=system_prompt,
        env={},
        agent_id=provider.meta.agent_id,
    )


@pytest.mark.asyncio
async def test_stateful_acp_bootstrap_is_sent_only_on_first_prompt(tmp_path: Any) -> None:
    session = _session(tmp_path, system_prompt="SESSION BOOTSTRAP")
    prompts: list[str] = []

    class ImmediateConnection:
        async def prompt(self, **kwargs: Any) -> Any:
            prompts.append(kwargs["prompt"][0].text)
            return SimpleNamespace(stop_reason="complete", usage=None)

    async def ready() -> None:
        return None

    session._connection = ImmediateConnection()  # type: ignore[assignment]
    session._acp_session_id = "session-1"
    session._ensure_subprocess = ready  # type: ignore[method-assign]

    assert [event async for event in session.send("task-1", "message one")]
    assert [event async for event in session.send("task-2", "message two")]

    assert prompts == [
        "[SYSTEM]\nSESSION BOOTSTRAP\n\n[USER]\nmessage one",
        "message two",
    ]


def test_harness_diagnostics_are_actionable_and_secret_redacted(tmp_path: Any) -> None:
    session = _session(tmp_path)
    session._stderr_lines.append(
        "details: 401 Invalid API-key; apiKey='opaque-test-credential-value'"
    )

    message, retryable = session._error_message(RuntimeError("Internal error"))

    assert "401 Invalid API-key" in message
    assert "credential-value" not in message
    assert retryable is False


def test_qwen_always_load_config_scrubs_model_credential(tmp_path: Any) -> None:
    session = _session(tmp_path)
    parent_env = {
        "PATH": "/usr/local/bin:/usr/bin",
        "OPENAI_API_KEY": "must-not-reach-mcp",
        "OPENAI_BASE_URL": "https://model.example/v1",
        "HTTPS_PROXY": "http://proxy.example:8080",
    }

    server = session._build_polynoia_mcp_server(parent_env)
    server_env = {item["name"]: item["value"] for item in server["env"]}
    assert server["command"]
    assert server["args"][:2] == ["-I", "-c"]
    assert "os.execve" in server["args"][2]
    assert server_env["PATH"] == "/usr/local/bin:/usr/bin"
    assert server_env["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert "OPENAI_API_KEY" not in server_env
    assert "OPENAI_BASE_URL" not in server_env

    qwen_home = tmp_path / "qwen-home"
    launch_env = {"QWEN_HOME": str(qwen_home)}
    session._prepare_provider_mcp_config(launch_env, server)
    config_path = qwen_home / "polynoia-mcp.json"
    config = json.loads(config_path.read_text())
    managed = config["mcpServers"]["polynoia"]
    assert managed["alwaysLoadTools"] is True
    assert managed["trust"] is False
    assert "must-not-reach-mcp" not in config_path.read_text()
    assert config_path.stat().st_mode & 0o077 == 0
    assert launch_env["POLYNOIA_MCP_CONFIG"] == str(config_path)


@pytest.mark.asyncio
async def test_permission_request_round_trips_selected_option() -> None:
    client = _AcpClient()
    queue = client.begin_turn("session-1")
    request = asyncio.create_task(
        client.request_permission(
            "session-1",
            ToolCallUpdate(toolCallId="call-1", title="Run tests", rawInput={"cmd": "pytest"}),
            [
                PermissionOption(optionId="allow", name="Allow once", kind="allow_once"),
                PermissionOption(optionId="deny", name="Reject", kind="reject_once"),
            ],
        )
    )

    notification = await asyncio.wait_for(queue.get(), timeout=1)
    permission_id = notification["params"]["permissionId"]
    assert notification["method"] == "_polynoia/permission"
    assert client.resolve_permission(permission_id, allow=True)
    response = await asyncio.wait_for(request, timeout=1)
    assert response.outcome.outcome == "selected"
    assert response.outcome.option_id == "allow"
    client.end_turn(queue)


@pytest.mark.asyncio
async def test_permission_reject_cannot_select_allow_option() -> None:
    client = _AcpClient()
    queue = client.begin_turn("session-1")
    request = asyncio.create_task(
        client.request_permission(
            "session-1",
            ToolCallUpdate(toolCallId="call-1", title="Write"),
            [
                PermissionOption(optionId="allow", name="Allow", kind="allow_once"),
                PermissionOption(optionId="deny", name="Reject", kind="reject_once"),
            ],
        )
    )
    notification = await asyncio.wait_for(queue.get(), timeout=1)
    permission_id = notification["params"]["permissionId"]

    assert not client.resolve_permission(permission_id, allow=False, option_id="allow")
    assert not request.done()
    assert client.resolve_permission(permission_id, allow=False, option_id="deny")
    response = await asyncio.wait_for(request, timeout=1)
    assert response.outcome.outcome == "selected"
    assert response.outcome.option_id == "deny"
    client.end_turn(queue)


@pytest.mark.asyncio
async def test_codex_native_permission_is_denied_without_ui_round_trip() -> None:
    client = _AcpClient(strict_mcp_only=True)
    queue = client.begin_turn("session-1")

    response = await client.request_permission(
        "session-1",
        ToolCallUpdate(
            toolCallId="native-command",
            title="Run shell",
            rawInput={"command": "touch forbidden"},
        ),
        [PermissionOption(optionId="allow", name="Allow", kind="allow_once")],
        field_meta={"codex": {"params": {"itemId": "native-command"}}},
    )

    assert response.outcome.outcome == "cancelled"
    notification = queue.get_nowait()
    assert notification["method"] == "_polynoia/policy_violation"
    assert "denied" in notification["params"]["message"]
    assert client._pending_permissions == {}
    client.end_turn(queue)


@pytest.mark.asyncio
async def test_permission_notification_translates_to_actionable_pap() -> None:
    event = [
        item
        async for item in translate_acp_stream_to_pap(
            _items(
                {
                    "method": "_polynoia/permission",
                    "params": {
                        "permissionId": "permission-1",
                        "toolCall": {
                            "toolCallId": "call-1",
                            "title": "Run tests",
                            "rawInput": {"cmd": "pytest"},
                        },
                        "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}],
                    },
                }
            ),
            turn_id="turn-1",
            task_id="task-1",
            provider="qwenCode",
        )
    ]

    assert len(event) == 1
    assert event[0].type == "permission.requested"
    assert event[0].provider == "qwenCode"
    assert event[0].tool_input == {"cmd": "pytest"}
    assert event[0].options[0]["optionId"] == "allow"


@pytest.mark.asyncio
async def test_qwen_permission_uses_mcp_name_and_decodes_json_input() -> None:
    events = [
        item
        async for item in translate_acp_stream_to_pap(
            _items(
                {
                    "method": "_polynoia/permission",
                    "params": {
                        "permissionId": "permission-qwen",
                        "toolCall": {
                            "toolCallId": "call-qwen",
                            "title": '{"path":"fixture.txt"}',
                            "rawInput": '{"path":"fixture.txt"}',
                            "_meta": {
                                "toolName": "mcp__polynoia__read",
                                "provenance": "mcp",
                                "serverId": "polynoia",
                            },
                        },
                        "options": [],
                    },
                }
            ),
            turn_id="turn-1",
            task_id="task-1",
            provider="qwenCode",
        )
    ]

    assert events[0].type == "permission.requested"
    assert events[0].tool_name == "mcp__polynoia__read"
    assert events[0].tool_input == {"path": "fixture.txt"}
    assert events[0].title == '{"path":"fixture.txt"}'


@pytest.mark.asyncio
async def test_qwen_native_tool_name_and_surface_are_preserved() -> None:
    events = [
        item
        async for item in translate_acp_stream_to_pap(
            _items(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "s",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "call-1",
                            "title": "Touch a file",
                            "status": "completed",
                            "rawInput": {"command": "touch ok"},
                            "rawOutput": "done",
                            "_meta": {"toolName": "Shell"},
                        },
                    },
                }
            ),
            turn_id="turn-1",
            task_id="task-1",
            provider="qwenCode",
        )
    ]

    payload = events[0].part
    assert payload.name == "Shell"
    assert payload.execution_surface == "harness-native"


@pytest.mark.asyncio
async def test_qwen_controlled_schema_search_is_polynoia_surface() -> None:
    events = [
        item
        async for item in translate_acp_stream_to_pap(
            _items(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "s",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "schema-1",
                            "title": "ToolSearch: Polynoia read",
                            "status": "completed",
                            "rawInput": {"query": "Polynoia read"},
                            "_meta": {
                                "toolName": "tool_search",
                                "provenance": "mcp",
                                "serverId": "polynoia",
                            },
                        },
                    },
                }
            ),
            turn_id="turn-1",
            task_id="task-1",
            provider="qwenCode",
        )
    ]

    assert events[0].part.name == "tool_search"
    assert events[0].part.execution_surface == "polynoia-mcp"


@pytest.mark.asyncio
async def test_polynoia_path_does_not_misclassify_codex_native_tool() -> None:
    events = [
        item
        async for item in translate_acp_stream_to_pap(
            _items(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "s",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "native-read",
                            "title": "Read file /tmp/work/.polynoia/file.txt",
                            "status": "completed",
                            "_meta": {
                                "codex": {
                                    "location": "/tmp/work/.polynoia/file.txt",
                                }
                            },
                        },
                    },
                }
            ),
            turn_id="turn-1",
            task_id="task-1",
            provider="codex",
        )
    ]

    assert events[0].part.execution_surface == "harness-native"


def test_codex_mcp_only_policy_allows_only_explicit_polynoia_envelope() -> None:
    allowed: set[str] = set()
    explicit_mcp = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "mcp-1",
                "title": "mcp.polynoia.write",
                "rawInput": {"server": "polynoia", "tool": "write"},
                "_meta": {"is_mcp_tool_call": True},
            }
        },
    }
    assert _mcp_only_policy_violation(explicit_mcp, allowed_call_ids=allowed) is None
    assert allowed == {"mcp-1"}
    assert (
        _mcp_only_policy_violation(
            {
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "mcp-1",
                        "status": "completed",
                    }
                },
            },
            allowed_call_ids=allowed,
        )
        is None
    )
    violation = _mcp_only_policy_violation(
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "native-1",
                    "title": "Read /tmp/.polynoia/file.txt",
                    "_meta": {"codex": {"cwd": "/tmp/.polynoia"}},
                }
            },
        },
        allowed_call_ids=allowed,
    )
    assert violation == "forbidden Harness-native tool call: Read /tmp/.polynoia/file.txt"

    controlled_bridge = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "deepseek-mcp-1",
                "title": "write",
                "rawInput": {"path": "result.txt"},
                "_meta": {"polynoia": {"source": "mcp", "server": "polynoia"}},
            }
        },
    }
    assert _mcp_only_policy_violation(controlled_bridge, allowed_call_ids=allowed) is None
    assert "deepseek-mcp-1" in allowed


def test_qwen_mcp_only_policy_allows_stamped_polynoia_and_rejects_native() -> None:
    allowed: set[str] = set()
    qwen_mcp = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "qwen-mcp-1",
                "title": "Read",
                "rawInput": {},
                "_meta": {
                    "toolName": "mcp__polynoia__read",
                    "provenance": "mcp",
                    "serverId": "polynoia",
                    "phase": "preparing",
                },
            }
        },
    }
    assert _mcp_only_policy_violation(qwen_mcp, allowed_call_ids=allowed) is None
    assert allowed == {"qwen-mcp-1"}

    control_plane = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "qwen-schema-1",
                "title": "ToolSearch: Polynoia read",
                "rawInput": {"query": "Polynoia read"},
                "_meta": {
                    "toolName": "tool_search",
                    "provenance": "mcp",
                    "serverId": "polynoia",
                },
            }
        },
    }
    assert _mcp_only_policy_violation(control_plane, allowed_call_ids=allowed) is None
    assert "qwen-schema-1" in allowed

    native = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "qwen-native-1",
                "title": "ToolSearch: read",
                "rawInput": {"query": "read"},
                "_meta": {
                    "toolName": "tool_search",
                    "provenance": "builtin",
                },
            }
        },
    }
    assert _mcp_only_policy_violation(native, allowed_call_ids=allowed) == (
        "forbidden Harness-native tool call: ToolSearch: read"
    )

    nested = {
        "method": "session/update",
        "params": {
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "child"},
                "_meta": {
                    "provenance": "subagent",
                    "parentToolCallId": "qwen-agent-1",
                    "subagentType": "general-purpose",
                },
            }
        },
    }
    assert _mcp_only_policy_violation(nested, allowed_call_ids=allowed) == (
        "forbidden Harness sub-agent event"
    )


@pytest.mark.asyncio
async def test_controlled_bridge_mcp_permission_reaches_ui_round_trip() -> None:
    client = _AcpClient(strict_mcp_only=True)
    queue = client.begin_turn("session-1")
    client._allowed_mcp_call_ids.add("deepseek-mcp-1")
    request = asyncio.create_task(
        client.request_permission(
            "session-1",
            ToolCallUpdate(toolCallId="deepseek-mcp-1", title="write"),
            [
                PermissionOption(optionId="allow", name="Allow", kind="allow_once"),
                PermissionOption(optionId="deny", name="Deny", kind="reject_once"),
            ],
            field_meta={"polynoia": {"source": "mcp", "server": "polynoia"}},
        )
    )
    enrichment = await asyncio.wait_for(queue.get(), timeout=1)
    assert enrichment["method"] == "session/update"
    assert enrichment["params"]["update"]["toolCallId"] == "deepseek-mcp-1"
    notification = await asyncio.wait_for(queue.get(), timeout=1)
    assert notification["method"] == "_polynoia/permission"
    permission_id = notification["params"]["permissionId"]
    assert client.resolve_permission(permission_id, allow=True, option_id="allow")
    response = await asyncio.wait_for(request, timeout=1)
    assert response.outcome.outcome == "selected"
    assert response.outcome.option_id == "allow"
    client.end_turn(queue)


@pytest.mark.asyncio
async def test_qwen_permission_enriches_preparing_tool_card_by_stable_id() -> None:
    client = _AcpClient(strict_mcp_only=True)
    queue = client.begin_turn("session-1")
    await client.session_update(
        "session-1",
        ToolCallStart.model_validate(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "qwen-mcp-1",
                "status": "pending",
                "title": "mcp__polynoia__read",
                "rawInput": {},
                "_meta": {
                    "toolName": "mcp__polynoia__read",
                    "provenance": "mcp",
                    "serverId": "polynoia",
                    "phase": "preparing",
                },
            }
        ),
    )
    preparing = await asyncio.wait_for(queue.get(), timeout=1)

    request = asyncio.create_task(
        client.request_permission(
            "session-1",
            ToolCallUpdate.model_validate(
                {
                    "toolCallId": "qwen-mcp-1",
                    "status": "pending",
                    "title": '{"path":"fixture.txt"}',
                    "rawInput": '{"path":"fixture.txt"}',
                }
            ),
            [PermissionOption(optionId="allow", name="Allow", kind="allow_once")],
        )
    )
    enrichment = await asyncio.wait_for(queue.get(), timeout=1)
    permission = await asyncio.wait_for(queue.get(), timeout=1)

    events = [
        event
        async for event in translate_acp_stream_to_pap(
            _items(preparing, enrichment, permission),
            turn_id="turn-1",
            task_id="task-1",
            provider="qwenCode",
        )
    ]
    tool_events = [event for event in events if event.type == "part.completed"]
    assert len(tool_events) == 2
    assert tool_events[0].part.input == {}
    assert tool_events[1].part.tool_call_id == tool_events[0].part.tool_call_id
    assert tool_events[1].part.name == "mcp__polynoia__read"
    assert tool_events[1].part.input == {"path": "fixture.txt"}
    permission_event = next(event for event in events if event.type == "permission.requested")
    assert permission_event.tool_name == "mcp__polynoia__read"
    assert permission_event.tool_input == {"path": "fixture.txt"}

    assert client.resolve_permission(
        permission_event.permission_id,
        allow=True,
        option_id="allow",
    )
    response = await asyncio.wait_for(request, timeout=1)
    assert response.outcome.outcome == "selected"
    client.end_turn(queue)


@pytest.mark.parametrize("meta_key", ["subagent", "collaboration"])
def test_codex_mcp_only_policy_rejects_nested_agent_events(meta_key: str) -> None:
    violation = _mcp_only_policy_violation(
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hidden activity"},
                    "_meta": {"codex": {meta_key: {"threadId": "child"}}},
                }
            },
        },
        allowed_call_ids=set(),
    )

    assert (
        violation == f"forbidden Codex {'sub-agent' if meta_key == 'subagent' else meta_key} event"
    )


@pytest.mark.asyncio
async def test_meta_is_preserved_and_secrets_are_redacted() -> None:
    events = [
        item
        async for item in translate_acp_stream_to_pap(
            _items(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "s",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "messageId": "m",
                            "content": {"type": "text", "text": "ok"},
                            "_meta": {
                                "codex": {"subagent": {"threadId": "child"}},
                                "apiKey": "must-not-leak",
                            },
                        },
                    },
                }
            ),
            turn_id="turn-1",
            task_id="task-1",
            provider="codex",
        )
    ]

    assert events[0].metadata["codex"]["subagent"]["threadId"] == "child"
    assert events[0].metadata["apiKey"] == "***"
    assert events[0].raw["_meta"]["apiKey"] == "***"


@pytest.mark.asyncio
async def test_qwen_contiguous_text_with_new_message_ids_is_one_part() -> None:
    updates = [
        {
            "method": "session/update",
            "params": {
                "sessionId": "s",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "messageId": message_id,
                    "content": {"type": "text", "text": chunk},
                },
            },
        }
        for message_id, chunk in (("m-1", "文件"), ("m-2", "已创建"), ("m-3", "。"))
    ]

    events = [
        item
        async for item in translate_acp_stream_to_pap(
            _items(*updates),
            turn_id="turn-1",
            task_id="task-1",
            provider="qwenCode",
        )
    ]

    starts = [item for item in events if isinstance(item, PartStartedEvent)]
    deltas = [item for item in events if isinstance(item, PartDeltaEvent)]
    completed = [item for item in events if isinstance(item, PartCompletedEvent)]
    assert len(starts) == 1
    assert len(deltas) == 3
    assert len(completed) == 1
    assert completed[0].message_id == starts[0].message_id
    assert completed[0].part.body[0].c == "文件已创建。"


@pytest.mark.asyncio
async def test_qwen_discrete_message_breaks_contiguous_text_part() -> None:
    def message(message_id: str, text: str, *, discrete: bool = False) -> dict[str, Any]:
        update: dict[str, Any] = {
            "sessionUpdate": "agent_message_chunk",
            "messageId": message_id,
            "content": {"type": "text", "text": text},
        }
        if discrete:
            update["_meta"] = {"qwenDiscreteMessage": True}
        return {"method": "session/update", "params": {"sessionId": "s", "update": update}}

    events = [
        item
        async for item in translate_acp_stream_to_pap(
            _items(
                message("m-1", "before"),
                message("m-2", "notice", discrete=True),
                message("m-3", "after"),
            ),
            turn_id="turn-1",
            task_id="task-1",
            provider="qwenCode",
        )
    ]

    completed = [
        item for item in events if isinstance(item, PartCompletedEvent) and item.part.kind == "text"
    ]
    assert [item.part.body[0].c for item in completed] == ["before", "notice", "after"]


@pytest.mark.asyncio
async def test_qwen_orphan_tool_update_breaks_contiguous_text_part() -> None:
    def message(message_id: str, text: str) -> dict[str, Any]:
        return {
            "method": "session/update",
            "params": {
                "sessionId": "s",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "messageId": message_id,
                    "content": {"type": "text", "text": text},
                },
            },
        }

    events = [
        item
        async for item in translate_acp_stream_to_pap(
            _items(
                message("m-1", "before"),
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "s",
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "call-1",
                            "title": "read",
                            "status": "completed",
                        },
                    },
                },
                message("m-2", "after"),
            ),
            turn_id="turn-1",
            task_id="task-1",
            provider="qwenCode",
        )
    ]

    completed = [
        item for item in events if isinstance(item, PartCompletedEvent) and item.part.kind == "text"
    ]
    assert [item.part.body[0].c for item in completed] == ["before", "after"]


@pytest.mark.asyncio
async def test_qwen_unprefixed_extension_notification_reaches_raw_stream(
    tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    accepted: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        asyncio.get_running_loop().create_future()
    )

    async def _accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.set_result((reader, writer))

    server = await asyncio.start_server(_accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client_reader, client_writer = await asyncio.open_connection("127.0.0.1", port)
    agent_reader, agent_writer = await accepted
    session = _session(tmp_path)
    client = session._client
    queue = client.begin_turn("session-1")
    connection = ClientSideConnection(
        client,
        client_writer,
        client_reader,
    )
    session._install_legacy_extension_routes(connection)
    params = {
        "v": 1,
        "sessionId": "session-1",
        "title": "Raw title",
        "nested": {"items": [1, 2, 3]},
    }
    caplog.set_level(logging.ERROR)
    try:
        agent_writer.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "qwen/notify/session/title-update",
                        "params": params,
                    }
                )
                + "\n"
            ).encode()
        )
        await agent_writer.drain()

        notification = await asyncio.wait_for(queue.get(), timeout=1)
        assert notification == {
            "jsonrpc": "2.0",
            "method": "qwen/notify/session/title-update",
            "params": params,
        }
        events = [
            event
            async for event in translate_acp_stream_to_pap(
                _items(notification),
                turn_id="turn-1",
                task_id="task-1",
                provider="qwenCode",
            )
        ]
        assert events[0].name == "qwen/notify/session/title-update"
        assert events[0].data == params
        assert events[0].raw["method"] == "qwen/notify/session/title-update"
        assert events[0].raw["params"] == params

        # The compatibility seam is notification-only. A non-standard request
        # with the same prefix must still fail closed instead of being swallowed.
        agent_writer.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "qwen/notify/session/title-update",
                        "params": params,
                    }
                )
                + "\n"
            ).encode()
        )
        await agent_writer.drain()
        response = json.loads(await asyncio.wait_for(agent_reader.readline(), timeout=1))
        assert response["id"] == 9
        assert response["error"]["code"] == -32601
        await asyncio.sleep(0)
        assert not any(
            record.levelno >= logging.ERROR
            and "qwen/notify/session/title-update" in record.getMessage()
            for record in caplog.records
        )
    finally:
        client.end_turn(queue)
        await connection.close()
        agent_writer.close()
        with contextlib.suppress(Exception):
            await agent_writer.wait_closed()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_close_joins_inflight_subprocess_reset_after_waiter_cancel(
    tmp_path: Any,
) -> None:
    class _BlockingProcessStack:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def aclose(self) -> None:
            self.calls += 1
            self.started.set()
            await self.release.wait()

    session = _session(tmp_path)
    stack = _BlockingProcessStack()
    session._process_stack = stack  # type: ignore[assignment]
    session._proc = SimpleNamespace(returncode=None)  # type: ignore[assignment]

    first_waiter = asyncio.create_task(session._reset_subprocess())
    await asyncio.wait_for(stack.started.wait(), timeout=1)
    shutdown = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    assert not shutdown.done()

    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter
    assert not shutdown.done()

    stack.release.set()
    await asyncio.wait_for(shutdown, timeout=1)
    assert stack.calls == 1
    assert session._reset_task is None


def test_all_requested_harnesses_are_registered_as_acp() -> None:
    assert {"claudeCode", "codex", "qwenCode", "deepseek"} <= set(ACP_PROVIDERS)
    assert ACP_PROVIDERS["claudeCode"].command == ("claude-agent-acp",)
    assert ACP_PROVIDERS["codex"].command == ("codex-acp",)
    assert ACP_PROVIDERS["codex"].tool_surface_policy == "polynoia-mcp-only"
    assert ACP_PROVIDERS["codex"].meta.capabilities.sub_agents is False
    assert ACP_PROVIDERS["qwenCode"].command[:5] == (
        "qwen",
        "--safe-mode",
        "--acp",
        "--approval-mode",
        "default",
    )
    assert "run_shell_command" in ACP_PROVIDERS["qwenCode"].command[-1]
    assert "computer_use__launch_app" in ACP_PROVIDERS["qwenCode"].command[-1]
    assert "tool_search" not in ACP_PROVIDERS["qwenCode"].command[-1].split(",")
    allowed_index = ACP_PROVIDERS["qwenCode"].command.index("--allowed-tools")
    assert ACP_PROVIDERS["qwenCode"].command[allowed_index + 1] == "mcp__polynoia__read"
    assert ACP_PROVIDERS["qwenCode"].pass_mcp_server is True
    assert ACP_PROVIDERS["qwenCode"].tool_surface_policy == "polynoia-mcp-only"
    assert ACP_PROVIDERS["qwenCode"].mcp_config_mode == "qwen-cli-always-load"
    assert ACP_PROVIDERS["qwenCode"].clear_mcp_parent_env is True
    assert ACP_PROVIDERS["deepseek"].command[0] == "dsh-acp-demo"
    deepseek = ACP_PROVIDERS["deepseek"]
    assert deepseek.pass_mcp_server is True
    assert deepseek.tool_surface_policy == "polynoia-mcp-only"
    assert deepseek.meta.capabilities.mcp is True
    assert deepseek.meta.capabilities.sub_agents is False
    assert deepseek.meta.capabilities.multi_session is False


def test_dsh_config_is_per_session_and_contains_no_secret(tmp_path: Any) -> None:
    contact_home = tmp_path / "private-deepseek-home"
    sandbox = SimpleNamespace(
        root=tmp_path,
        agent_runtime_home=lambda _adapter_id: contact_home,
    )
    context = SimpleNamespace(
        sandbox=sandbox,
        cwd=str(tmp_path),
        model="deepseek-v4-flash-0731",
        skills=(),
    )
    env = {"DEEPSEEK_API_KEY": "secret", "DEEPSEEK_BASE_URL": "https://example.test/v1"}

    _prepare_dsh_environment(context, env)

    config = (contact_home / "dsh-acp.cordis.yml").read_text()
    assert "deepseek-v4-flash-0731" in config
    assert "secret" not in config
    assert str(contact_home / "sessions") in config
    assert env["POLYNOIA_ACP_CONFIG"].endswith("dsh-acp.cordis.yml")
    assert "DSH_AGENTS_HOME" not in env
    assert "DSH_PERMISSION_MODE" not in env


def test_codex_contact_endpoint_auth_stays_out_of_logs_and_config(tmp_path: Any) -> None:
    source_codex_home = tmp_path / "source-codex-home"
    source_codex_home.mkdir()
    (source_codex_home / "auth.json").write_text('{"token":"do-not-copy-to-config"}')
    contact_home = tmp_path / "contact-home"
    context = SimpleNamespace(
        sandbox=SimpleNamespace(
            root=tmp_path,
            agent_runtime_home=lambda _adapter_id: contact_home,
        ),
        model="qwen3.6-flash",
    )
    env = {
        "OPENAI_API_KEY": "secret",
        "OPENAI_BASE_URL": "https://gateway.example/v1",
        "CODEX_HOME": str(source_codex_home),
        "INITIAL_AGENT_MODE": "agent-full-access",
        "CODEX_CONFIG": json.dumps(
            {
                "sandbox_mode": "danger-full-access",
                "features": {
                    "multi_agent": True,
                    "multi_agent_v2": True,
                    "shell_tool": True,
                    "unified_exec": True,
                },
            }
        ),
    }

    _prepare_codex_environment(context, env)

    config = json.loads(env["CODEX_CONFIG"])
    assert env["INITIAL_AGENT_MODE"] == "read-only"
    assert env["CODEX_HOME"] == str(contact_home / ".codex")
    assert (contact_home / ".codex" / "auth.json").is_file()
    assert "do-not-copy-to-config" not in (contact_home / ".codex" / "config.toml").read_text()
    assert "APP_SERVER_LOGS" not in env
    assert "secret" not in env["CODEX_CONFIG"]
    assert config["model_reasoning_effort"] == "low"
    assert config["model"] == "qwen3.6-flash"
    assert config["sandbox_mode"] == "read-only"
    assert config["approval_policy"] == "never"
    assert config["mcp_servers"] == {}
    assert config["tools"] == {"view_image": False, "web_search": False}
    assert config["apps"] == {"_default": {"enabled": False}}
    assert config["skills"] == {"config": []}
    for feature in (
        "multi_agent",
        "multi_agent_v2",
        "shell_tool",
        "unified_exec",
        "browser_use",
        "computer_use",
    ):
        assert config["features"][feature] is False
    assert _codex_authentication_request(env) == (
        "gateway",
        {
            "gateway": {
                "baseUrl": "https://gateway.example/v1",
                "headers": {"Authorization": "Bearer secret"},
                "providerName": "Polynoia contact endpoint",
            }
        },
    )


@pytest.mark.asyncio
async def test_setup_failure_becomes_actionable_terminal_event(tmp_path: Any) -> None:
    from polynoia.adapters.acp import GenericAcpSession
    from polynoia.adapters.acp_providers import CODEX_ACP_PROVIDER

    sandbox = SimpleNamespace(
        conv_id="conv",
        root=tmp_path,
        workspace_root=None,
        workspace_id=None,
        env_for_agent=lambda env: dict(env),
    )
    session = GenericAcpSession(
        provider=CODEX_ACP_PROVIDER,
        sandbox=sandbox,
        conv_id="conv",
        cwd=str(tmp_path),
        model=None,
        system_prompt=None,
        env={},
        agent_id="codex",
    )

    async def fail_setup() -> None:
        raise RuntimeError("Authentication required")

    session._ensure_subprocess = fail_setup  # type: ignore[method-assign]
    events = [event async for event in session.send("task", "hello")]

    assert [event.type for event in events] == ["turn.started", "turn.failed"]
    assert "codex-acp login" in events[1].error["message"]
    assert events[1].error["retryable"] is False


@pytest.mark.asyncio
async def test_terminal_chunk_releases_session_lock_with_source_retained(tmp_path: Any) -> None:
    session = _session(tmp_path)

    async def locked_turn(
        task_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Any]:
        del text, attachments
        async with session._lock:
            yield TurnStartedEvent(turn_id="turn-1", task_id=task_id, provider="qwenCode")
            yield TurnCompletedEvent(
                turn_id="turn-1",
                task_id=task_id,
                provider="qwenCode",
            )

    session._send_locked = locked_turn  # type: ignore[method-assign]
    source = session.send("task-1", "hello")
    chunks = adapter_events_to_chunks(
        source,
        agent_id="qwenCode",
        conv_id="conv-1",
    )

    assert [chunk async for chunk in chunks]
    assert source is not None and chunks is not None
    assert session.is_busy is False
    await asyncio.wait_for(session._lock.acquire(), timeout=0.1)
    session._lock.release()


@pytest.mark.asyncio
async def test_aclose_mid_prompt_cancels_and_resets_runtime(tmp_path: Any) -> None:
    session = _session(tmp_path)
    cancel_called = asyncio.Event()
    prompt_cancelled = asyncio.Event()

    class PendingConnection:
        async def prompt(self, **kwargs: Any) -> Any:
            del kwargs
            queue = session._client._active_queue
            assert queue is not None
            await queue.put(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-1",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "messageId": "message-1",
                            "content": {"type": "text", "text": "partial"},
                        },
                    },
                }
            )
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                prompt_cancelled.set()
                raise

        async def cancel(self, **kwargs: Any) -> None:
            del kwargs
            cancel_called.set()

    async def ready() -> None:
        return None

    session._connection = PendingConnection()  # type: ignore[assignment]
    session._acp_session_id = "session-1"
    session._ensure_subprocess = ready  # type: ignore[method-assign]

    source = session.send("task-1", "hello")
    assert (await source.__anext__()).type == "turn.started"
    assert (await source.__anext__()).type == "part.started"
    await source.aclose()

    assert cancel_called.is_set()
    assert prompt_cancelled.is_set()
    assert session._connection is None
    assert session.is_busy is False


@pytest.mark.asyncio
async def test_codex_native_tool_event_cancels_resets_and_fails_turn(tmp_path: Any) -> None:
    session = _session(tmp_path, provider=CODEX_ACP_PROVIDER)
    cancel_called = asyncio.Event()
    prompt_cancelled = asyncio.Event()

    class NativeToolConnection:
        async def prompt(self, **kwargs: Any) -> Any:
            del kwargs
            queue = session._client._active_queue
            assert queue is not None
            await queue.put(
                {
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-1",
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "native-command",
                            "title": "touch forbidden.txt",
                            "status": "in_progress",
                            "rawInput": {"command": "touch forbidden.txt"},
                        },
                    },
                }
            )
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                prompt_cancelled.set()
                raise

        async def cancel(self, **kwargs: Any) -> None:
            del kwargs
            cancel_called.set()

    async def ready() -> None:
        return None

    session._connection = NativeToolConnection()  # type: ignore[assignment]
    session._acp_session_id = "session-1"
    session._ensure_subprocess = ready  # type: ignore[method-assign]

    events = [event async for event in session.send("task-1", "make a file")]

    assert [event.type for event in events] == ["turn.started", "turn.failed"]
    assert events[-1].error == {
        "subtype": "harness_policy_violation",
        "message": "forbidden Harness-native tool call: touch forbidden.txt",
        "retryable": False,
    }
    assert cancel_called.is_set()
    assert prompt_cancelled.is_set()
    assert session._connection is None
    assert session.is_busy is False
