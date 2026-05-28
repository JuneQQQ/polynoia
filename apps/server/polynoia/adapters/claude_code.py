"""Claude Code adapter — wraps the Anthropic Claude Code CLI.

Uses `claude_agent_sdk.ClaudeSDKClient` which internally spawns `claude` with
the bidirectional stream-json protocol. The SDK gives us typed `Message` objects
(AssistantMessage / UserMessage / SystemMessage / ResultMessage / StreamEvent),
which we translate into Polynoia `AdapterEvent`s.

Translation map (Claude Code → PAP):
  StreamEvent.event(`content_block_delta` w/ text_delta) → PartDeltaEvent
  AssistantMessage.content[TextBlock]                    → PartCompletedEvent(TextPayload)
  AssistantMessage.content[ToolUseBlock]                 → PartCompletedEvent(TypingPayload)
                                                            (P0 — 后续可按 tool name 转成 DiffPayload 等)
  AssistantMessage.content[ThinkingBlock]                → 暂跳过 (P1 加 thinking part)
  ResultMessage(success)                                  → TurnCompletedEvent
  ResultMessage(error)                                    → TurnFailedEvent
  RateLimitEvent                                          → RateLimitEvent (pass-through)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import McpStdioServerConfig

from polynoia.adapters._utils import _new_id, _stringify_tool_output, _tool_summary
from polynoia.adapters.base import (
    AdapterCapabilities,
    AdapterEvent,
    AdapterMeta,
    PartCompletedEvent,
    PartDeltaEvent,
    PartStartedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from polynoia.domain.messages import TextBlock as PNTextBlock
from polynoia.domain.messages import TextPayload, ToolCallPayload
from polynoia.sandbox import Sandbox


class ClaudeCodeAdapter:
    """Adapter for the `@anthropic-ai/claude-code` CLI."""

    def __init__(self):
        self.meta = AdapterMeta(
            agent_id="claudeCode",
            cli_command="claude",
            detected=False,
            auth_kinds=["cli-login", "api-key"],
            base_model="claude-opus-4-7",
            docs="https://docs.claude.com/code",
            capabilities=AdapterCapabilities(
                streaming=True,
                tool_calling="native",
                permissions=True,
                hooks=[
                    "pre_tool",
                    "post_tool",
                    "user_prompt_submit",
                    "stop",
                    "subagent_start",
                    "subagent_stop",
                    "pre_compact",
                    "permission_request",
                    "notification",
                ],
                sub_agents=True,
                mcp=True,
                file_edit_formats=["search-replace", "whole"],
            ),
        )

    async def detect(self) -> tuple[bool, str | None]:
        path = shutil.which("claude")
        if not path:
            return False, None
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            line = stdout.decode().strip().splitlines()[0] if stdout else ""
            # Typical: "2.1.143 (Claude Code)"
            version = line.split()[0] if line else None
            self.meta.detected = True
            self.meta.detected_version = version
            return True, version
        except (TimeoutError, FileNotFoundError, subprocess.CalledProcessError):
            return False, None

    async def start_session(
        self,
        conv_id: str,
        cwd: str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        env: dict[str, str] | None = None,
        workspace_id: str | None = None,
        agent_id: str | None = None,
        merge_mode: str = "auto",
    ) -> ClaudeCodeSession:
        # P1.1 routing — group convs in a workspace share git via worktrees,
        # everything else uses legacy per-conv sandbox.
        if workspace_id and agent_id:
            sandbox = await Sandbox.create_workspace_sandbox(
                workspace_id=workspace_id, conv_id=conv_id, agent_id=agent_id,
            )
        else:
            sandbox = await Sandbox.create(conv_id)
        effective_cwd = cwd or str(sandbox.root)

        # Register the Polynoia MCP server. Claude Code spawns it as a stdio
        # subprocess; POLYNOIA_CONV_ID + POLYNOIA_AGENT_ID bind that MCP instance
        # to this (conv, agent). PYTHONPATH ensures the spawned interpreter can
        # import the polynoia package even though it inherits a sandboxed HOME.
        server_pkg_root = str(Path(__file__).parent.parent.parent)
        # POLYNOIA_API_BASE lets MCP tools call back into the FastAPI server
        # (needed for manual-mode pending-edit gating; see ADR-005).
        # Default to local 8000 — adapter consumers can override via env.
        api_base = os.environ.get("POLYNOIA_API_BASE", "http://127.0.0.1:8000")
        polynoia_mcp = McpStdioServerConfig(
            type="stdio",
            command="python",
            args=["-m", "polynoia.mcp"],
            env={
                "POLYNOIA_CONV_ID": conv_id,
                "POLYNOIA_AGENT_ID": self.meta.agent_id,
                # IMPORTANT: MCP subprocess inherits Claude SDK's sandboxed
                # HOME, so Path.home() resolves wrong. Pin sandbox_root via env.
                "POLYNOIA_SANDBOX_ROOT": str(sandbox.root.parent),
                "POLYNOIA_API_BASE": api_base,
                "PYTHONPATH": server_pkg_root,
            },
        )

        sandbox_env = sandbox.env_for_agent(env or {})
        # Default allowed_tools = all Polynoia MCP tools, so Claude Code's
        # permission prompt doesn't gate every tool call. The sandbox boundary
        # is already enforced by Polynoia MCP (writes confined to sandbox cwd).
        #
        # IMPORTANT: built-in Edit / Write / MultiEdit / NotebookEdit are
        # NOT in this list. Every file mutation flows through Polynoia MCP
        # (mcp__polynoia__edit/write/apply_patch), which lets us:
        #   · audit every write (.polynoia/audit.jsonl)
        #   · enforce sandbox boundary
        #   · GATE on user approval in manual merge_mode (ADR-005)
        # `merge_mode` is currently informational — gating is already
        # automatic because Polynoia MCP is the only write path. Kept as
        # an explicit parameter for future divergence (e.g. allow auto-mode
        # users to opt into built-in tools for performance).
        _ = merge_mode  # currently informational, see comment above
        default_allowed = [
            # Polynoia MCP tools — sandboxed + auditable + gateable
            "mcp__polynoia__read",
            "mcp__polynoia__edit",
            "mcp__polynoia__write",
            "mcp__polynoia__apply_patch",
            "mcp__polynoia__bash",
            "mcp__polynoia__grep",
            "mcp__polynoia__glob",
            "mcp__polynoia__revert",
            "mcp__polynoia__call_agent",
            # Built-in web tools — free for Pro, read-only so gate-irrelevant
            "WebFetch",
            "WebSearch",
        ]
        effective_allowed = allowed_tools if allowed_tools else default_allowed
        # System prompt strategy: NEVER override Claude Code's built-in default
        # (which contains tool-use instructions, TodoList behavior, file ops
        # convention, etc.). Instead use the SDK's `preset + append` form so
        # the user persona EXTENDS the default. If no user persona is set,
        # pass system_prompt=None — SDK defaults stay clean.
        sys_prompt_param: str | dict[str, Any] | None
        if system_prompt and system_prompt.strip():
            sys_prompt_param = {
                "type": "preset",
                "preset": "claude_code",
                "append": system_prompt,
            }
        else:
            sys_prompt_param = None
        opts = ClaudeAgentOptions(
            cwd=effective_cwd,
            system_prompt=sys_prompt_param,
            allowed_tools=effective_allowed,
            permission_mode="bypassPermissions",   # MCP boundary suffices
            model=model,
            env=sandbox_env,
            # 关键:开启流式 + include partial messages 让我们拿到 text-delta
            include_partial_messages=True,
            mcp_servers={"polynoia": polynoia_mcp},
        )
        return ClaudeCodeSession(
            opts=opts,
            agent_id=self.meta.agent_id,
            sandbox=sandbox,
        )


def _human_api_error(status: int) -> str:
    """Translate Anthropic upstream HTTP errors into actionable messages.

    These show up in ResultMessage.api_error_status when the SDK couldn't
    reach a clean turn completion — surface them to the user instead of the
    SDK-quirky "subtype=success" placeholder.
    """
    table: dict[int, str] = {
        400: "Anthropic API 400:请求格式错误(可能是 prompt 超长 / 工具 schema 不合规)",
        401: "Anthropic API 401:凭证失效,请重新登录 `claude /login`",
        402: "Anthropic API 402:账户额度不足或需要付款",
        403: "Anthropic API 403:权限不足(可能是组织 / 模型未授权)",
        404: "Anthropic API 404:模型不存在(检查 contact 的 model id)",
        408: "Anthropic API 408:请求超时,请重试",
        413: "Anthropic API 413:请求过大,prompt + history 超过 model 上下文限制",
        429: "Anthropic API 429:被限速。Pro 月配额耗尽或 RPS 超限,请稍后重试",
        500: "Anthropic API 500:upstream 内部错误,请稍后重试",
        502: "Anthropic API 502:upstream 网关错误,请稍后重试",
        503: "Anthropic API 503:upstream 暂时不可用,请稍后重试",
        529: "Anthropic API 529:upstream 过载(常见峰值时段),请稍后重试",
    }
    return table.get(status, f"Anthropic API {status}:upstream 错误")


async def _translate_claude_stream(
    messages: AsyncIterator[Any],
    turn_id: str,
    task_id: str,
) -> AsyncIterator[AdapterEvent]:
    """Translate claude_agent_sdk Message stream into PAP AdapterEvent stream.

    Pure async generator — no subprocess coupling. Tests feed canned messages
    directly via an async iterator wrapper.
    """
    # State for stream-event → delta translation
    # Claude Code emits `StreamEvent`(原 Anthropic SSE)+ final `AssistantMessage`.
    # We map content_block_start/delta/stop → PartStartedEvent/PartDeltaEvent/PartCompletedEvent.
    current_message_id: str | None = None
    # block_idx → part_id (one per content block in current assistant message)
    block_to_part: dict[int, str] = {}
    block_text: dict[int, str] = {}
    # tool_call_id → (message_id, part_id) so we can complete the same card
    # when the user message (tool_result) comes back from Claude Code.
    tool_call_part_id: dict[str, tuple[str, str]] = {}
    tool_call_payload: dict[str, ToolCallPayload] = {}

    # Diagnostic: when POLYNOIA_LOG_CLAUDE_EVENTS=1, print every SDK message
    # type as it arrives. Useful for debugging "lost streaming" — if we see
    # no StreamEvent and only AssistantMessage, partial messages aren't on.
    import os
    _debug = os.environ.get("POLYNOIA_LOG_CLAUDE_EVENTS") == "1"

    async for msg in messages:
        if _debug:
            cls = type(msg).__name__
            if cls == "StreamEvent":
                ev_type = (msg.event or {}).get("type") if hasattr(msg, "event") else "?"
                print(f"[claude-sdk] StreamEvent {ev_type}", flush=True)
            else:
                print(f"[claude-sdk] {cls}", flush=True)

        # ── StreamEvent: raw SSE from Anthropic API ────
        if isinstance(msg, StreamEvent):
            ev = msg.event or {}
            ev_type = ev.get("type")
            if ev_type == "message_start":
                current_message_id = (
                    ev.get("message", {}).get("id") or _new_id()
                )
                block_to_part.clear()
                block_text.clear()
            elif ev_type == "content_block_start":
                idx = ev.get("index", 0)
                block = ev.get("content_block", {})
                btype = block.get("type")
                if btype == "text":
                    part_id = _new_id()
                    block_to_part[idx] = part_id
                    block_text[idx] = ""
                    yield PartStartedEvent(
                        turn_id=turn_id,
                        task_id=task_id,
                        message_id=current_message_id or _new_id(),
                        part_id=part_id,
                        part=TextPayload(body=[PNTextBlock(c="")]),
                    )
                # thinking / tool_use: 先不发 part.started,等结束后看 AssistantMessage 整体发卡
            elif ev_type == "content_block_delta":
                idx = ev.get("index", 0)
                delta = ev.get("delta", {})
                if delta.get("type") == "text_delta":
                    existing_part_id = block_to_part.get(idx)
                    if existing_part_id:
                        chunk = delta.get("text", "")
                        block_text[idx] = block_text.get(idx, "") + chunk
                        yield PartDeltaEvent(
                            message_id=current_message_id or _new_id(),
                            part_id=existing_part_id,
                            delta={"text": chunk},
                        )
            elif ev_type == "content_block_stop":
                idx = ev.get("index", 0)
                existing_part_id = block_to_part.get(idx)
                if existing_part_id:
                    final_text = block_text.get(idx, "")
                    yield PartCompletedEvent(
                        message_id=current_message_id or _new_id(),
                        part_id=existing_part_id,
                        part=TextPayload(body=[PNTextBlock(c=final_text)]),
                    )
            # message_delta / message_stop: 不发(等 ResultMessage)
            continue

        # ── AssistantMessage: 最终聚合 ────
        # tool_use 块在这里来(stream_event 里我们没发过 tool part)
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    # 通用 tool-call 卡,state=running(等 tool_result 来再改 completed)
                    # 每个 tool 自己的 message_id — 否则同一 turn 多个 tool 会在前端 store 互相覆盖
                    tc_msg_id = _new_id()
                    part_id = _new_id()
                    payload = ToolCallPayload(
                        tool_call_id=block.id,
                        name=block.name,
                        input=block.input or {},
                        state="running",
                        summary=_tool_summary(block.name, block.input),
                    )
                    tool_call_part_id[block.id] = (tc_msg_id, part_id)
                    tool_call_payload[block.id] = payload
                    yield PartCompletedEvent(
                        message_id=tc_msg_id,
                        part_id=part_id,
                        part=payload,
                    )
                elif isinstance(block, ThinkingBlock):
                    # P0:暂不渲(P1 加 thinking part type)
                    pass
                elif isinstance(block, TextBlock):
                    # 已经经 stream_event 流出过了,跳过避免重复
                    pass
            continue

        # ── UserMessage(tool_result):服务端把 tool 结果送回来 ────
        if isinstance(msg, UserMessage):
            # Match tool_result back to the tool_call we emitted earlier;
            # re-emit the same part_id with state=completed/error + output.
            content = msg.content if isinstance(msg.content, list) else []
            for block in content:
                if isinstance(block, ToolResultBlock):
                    tc_id = block.tool_use_id
                    meta = tool_call_part_id.get(tc_id)
                    prev = tool_call_payload.get(tc_id)
                    if meta is None or prev is None:
                        continue
                    mid, pid = meta
                    # block.content can be str, list of TextBlock-like dicts, or other
                    out_text = _stringify_tool_output(block.content)
                    updated = prev.model_copy(update={
                        "state": "error" if block.is_error else "completed",
                        "is_error": bool(block.is_error),
                        "output": block.content,
                        "output_text": out_text,
                    })
                    tool_call_payload[tc_id] = updated
                    yield PartCompletedEvent(
                        message_id=mid,
                        part_id=pid,
                        part=updated,
                    )
            continue

        # ── SystemMessage(init / mcp_status / etc):noop in P0 ──
        if isinstance(msg, SystemMessage):
            continue

        # ── ResultMessage: turn 终结 ────
        if isinstance(msg, ResultMessage):
            if msg.is_error:
                # SDK quirk (v0.2.87+ ResultMessage spec):
                #   When the upstream Anthropic API call itself failed (429,
                #   500, 529, ...), the SDK emits:
                #       is_error=True
                #       subtype="success"   ← misleading,literally the string
                #       api_error_status=<HTTP code>
                #   If we naively use subtype as the error text, user sees
                #   the meaningless message "Error: success".
                #   `api_error_status` is the real signal. If it's set, render
                #   a useful upstream-API error message.
                api_status = getattr(msg, "api_error_status", None)
                if api_status:
                    message = _human_api_error(api_status)
                elif msg.subtype and msg.subtype != "success":
                    message = msg.subtype
                else:
                    # Fall back: include the underlying SDK error list if any
                    errors = getattr(msg, "errors", None) or []
                    message = (
                        "; ".join(str(e) for e in errors)
                        if errors
                        else "agent turn failed (no further detail)"
                    )
                yield TurnFailedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    error={
                        "subtype": msg.subtype,
                        "duration_ms": msg.duration_ms,
                        "api_error_status": api_status,
                        "message": message,
                    },
                )
            else:
                yield TurnCompletedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    usage=msg.usage or {},
                    cost_usd=msg.total_cost_usd or 0.0,
                    duration_ms=msg.duration_ms or 0,
                    stop_reason=msg.subtype or "complete",
                )
            return


class ClaudeCodeSession:
    """One Claude Code session (one subprocess, multi-turn)."""

    def __init__(
        self,
        opts: ClaudeAgentOptions,
        agent_id: str,
        sandbox: Sandbox | None = None,
        cleanup_on_close: bool = False,
    ):
        self.session_id = _new_id()
        self.agent_id = agent_id
        self._opts = opts
        self._sandbox = sandbox
        self._cleanup_on_close = cleanup_on_close
        self._client: ClaudeSDKClient | None = None
        self._lock = asyncio.Lock()  # serialize send() calls per session

    async def _ensure_client(self) -> ClaudeSDKClient:
        if self._client is None:
            self._client = ClaudeSDKClient(options=self._opts)
            await self._client.connect()
        return self._client

    async def send(
        self,
        task_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AdapterEvent]:
        """Send a user message; yield AdapterEvents until the turn finishes."""
        async with self._lock:
            client = await self._ensure_client()
            turn_id = _new_id()

            yield TurnStartedEvent(turn_id=turn_id, task_id=task_id)

            # Send the user query
            await client.query(text)

            try:
                async for ev in _translate_claude_stream(
                    client.receive_response(), turn_id, task_id
                ):
                    yield ev
            except Exception as e:
                yield TurnFailedEvent(
                    turn_id=turn_id,
                    task_id=task_id,
                    error={"subtype": "exception", "message": str(e)},
                )

    async def respond_permission(
        self,
        permission_id: str,
        allow: bool,
        updated_input: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        # P0 stub: 自动 allow,真正的 can_use_tool 回调 P1+ 接
        return

    async def interrupt(self, task_id: str | None = None) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.interrupt()

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.disconnect()
            self._client = None
        if self._cleanup_on_close and self._sandbox is not None:
            with contextlib.suppress(Exception):
                await self._sandbox.cleanup()
