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
import sys
import time
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

from polynoia.adapters._utils import (
    _new_id,
    _reasoning_seconds,
    _stringify_tool_output,
    _tool_summary,
    apply_proxy_egress,
)
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
from polynoia.domain.messages import ReasoningPayload, TextPayload, ToolCallPayload
from polynoia.credentials import use_direct_host_credentials
from polynoia.mcp.tools import tools_for_role
from polynoia.sandbox import Sandbox
from polynoia.settings import settings


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
        tool_role: str = "generalist",
        tools_whitelist: list[str] | None = None,
        read_only_workspace_id: str | None = None,
        proxy: str | None = None,
        proxy_kind: str = "system",
        skills: list[str] | None = None,
    ) -> ClaudeCodeSession:
        # P1.1 routing — group convs in a workspace share git via worktrees.
        # Project access for DMs is explicit; there is no read-only fallback role.
        if workspace_id and agent_id:
            sandbox = await Sandbox.create_workspace_sandbox(
                workspace_id=workspace_id, conv_id=conv_id, agent_id=agent_id,
            )
        elif read_only_workspace_id:
            sandbox = Sandbox.open_workspace_if_exists(
                read_only_workspace_id
            ) or await Sandbox.create(conv_id)
        else:
            sandbox = await Sandbox.create(conv_id)
        effective_cwd = cwd or str(sandbox.root)

        # Contact-level skill packages: copy each bound skill folder into the
        # sandbox's native ~/.claude/skills/ so the underlying Claude CLI
        # discovers them (progressive disclosure) and can run their scripts.
        if skills:
            await sandbox.place_skill_packages(skills)

        # Register the Polynoia MCP server. Claude Code spawns it as a stdio
        # subprocess; POLYNOIA_CONV_ID + POLYNOIA_AGENT_ID bind that MCP instance
        # to this (conv, agent). PYTHONPATH ensures the spawned interpreter can
        # import the polynoia package even though it inherits a sandboxed HOME.
        server_pkg_root = str(Path(__file__).parent.parent.parent)
        # POLYNOIA_API_BASE lets MCP tools call back into the FastAPI server
        # (manual-mode pending-edit gating + the `dispatch` tool). Default is
        # derived from settings.port (the canonical port, 7780) so it's correct
        # regardless of launch method; an explicit env var still overrides.
        api_base = os.environ.get(
            "POLYNOIA_API_BASE", f"http://127.0.0.1:{settings.port}"
        )
        polynoia_mcp = McpStdioServerConfig(
            type="stdio",
            # sys.executable, NOT bare "python": the MCP subprocess must run on
            # the SAME interpreter as the server (the venv that has mcp/fastapi/
            # polynoia installed). Bare "python" resolves via the spawning
            # process's PATH — if the server wasn't launched with the venv's bin
            # first on PATH (e.g. `./.venv/bin/uvicorn` without activation, or a
            # pyenv shim shadowing it), `python -m polynoia.mcp` crashes on
            # `import mcp` → the SDK loads ZERO tools → the agent, having no real
            # tools, narrates tool calls as TEXT instead of invoking them.
            command=sys.executable,
            args=["-m", "polynoia.mcp"],
            env={
                "POLYNOIA_CONV_ID": conv_id,
                "POLYNOIA_AGENT_ID": self.meta.agent_id,
                # The per-turn worker ULID (contact), not the static adapter id —
                # so proactive diff cards attribute to this agent + its lane.
                "POLYNOIA_TURN_AGENT_ID": agent_id or self.meta.agent_id,
                "POLYNOIA_AGENT_ROLE": tool_role,
                # Per-contact tool override (narrows the role set; empty = role default).
                "POLYNOIA_AGENT_TOOLS": ",".join(tools_whitelist or []),
                # IMPORTANT: MCP subprocess inherits Claude SDK's sandboxed
                # HOME, so Path.home() resolves wrong. Pin sandbox_root via env.
                "POLYNOIA_SANDBOX_ROOT": str(sandbox.root.parent),
                # The EXACT worktree this agent runs in, so MCP tools write +
                # commit to the agent's own branch (not a separate per-conv
                # sandbox). Only set in workspace mode.
                # POLYNOIA_WORKSPACE_ID is what `present` reads to build the
                # card's src URL. Listed explicitly so this doesn't depend on
                # claude_agent_sdk's parent-env inheritance (which opencode +
                # codex don't have). Keeps all three adapters symmetrical.
                **(
                    {
                        "POLYNOIA_WORKSPACE_ID": sandbox.workspace_id or "",
                        "POLYNOIA_WORKTREE_ROOT": str(sandbox.root),
                        "POLYNOIA_WORKSPACE_ROOT": str(sandbox.workspace_root),
                    }
                    if sandbox.workspace_root
                    else {}
                ),
                "POLYNOIA_API_BASE": api_base,
                "PYTHONPATH": server_pkg_root,
            },
        )

        # IS_SANDBOX=1 tells the claude CLI it's running inside a wrapping
        # sandbox, which suppresses its "--dangerously-skip-permissions cannot
        # be used with root/sudo" safety abort. We legitimately need that flag
        # (permission_mode="bypassPermissions" below) because Polynoia's MCP
        # already enforces the write boundary — but the CLI refuses it under
        # root unless this signal is set. Common case: WSL2 + dev-as-root.
        #
        # Narrowed to root-only because IS_SANDBOX is an undocumented CLI env
        # knob — setting it unconditionally could change other CLI behavior
        # (telemetry, prompts) for the majority of devs whose setup already
        # works. Non-root keeps the unmodified original code path.
        extra_env = dict(env or {})
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            extra_env.setdefault("IS_SANDBOX", "1")
        # Proxy egress (system inherit / direct strip / custom override) — shared
        # across all three adapters; system relies on env_for_agent copying host
        # proxy vars from os.environ.
        extra_env = apply_proxy_egress(extra_env, proxy_kind, proxy)
        sandbox_env = sandbox.env_for_agent(extra_env)
        # ── merged: feat used `env_for_agent(env or {})`; main's root-aware
        #    IS_SANDBOX path above is a strict superset, so it wins. ──
        # Default allowed_tools = all Polynoia MCP tools, so Claude Code's
        # permission prompt doesn't gate every tool call. The sandbox boundary
        # is already enforced by Polynoia MCP (writes confined to sandbox cwd).
        #
        # IMPORTANT: built-in Edit / Write / MultiEdit / NotebookEdit are
        # NOT in this list. Every file mutation flows through Polynoia MCP
        # (mcp__polynoia__write — the sole mutation tool), which lets us:
        #   · audit every write (.polynoia/audit.jsonl)
        #   · enforce sandbox boundary
        #   · GATE on user approval in manual merge_mode (ADR-005)
        # `merge_mode` is currently informational — gating is already
        # automatic because Polynoia MCP is the only write path. Kept as
        # an explicit parameter for future divergence (e.g. allow auto-mode
        # users to opt into built-in tools for performance).
        _ = merge_mode  # currently informational, see comment above
        # The ONLY tools an agent may use are the role-scoped Polynoia MCP
        # tools. `allowed_tools` is just a permission whitelist (auto-approve);
        # it does NOT restrict availability. Availability is governed by:
        #   · tools=[]            → disable every built-in (Bash/Read/Edit/
        #                           Skill/ToolSearch/WebFetch/…). Without this
        #                           the agent inherits the full Claude Code
        #                           toolset and any user-installed skills.
        #   · setting_sources=[]  → don't load ~/.claude settings, so the
        #                           parent's plugins/skills (e.g. karpathy)
        #                           never leak into the sub-agent.
        #   · strict_mcp_config   → ignore project/user/plugin MCP servers;
        #                           only our `polynoia` server is mounted.
        # Net effect: every file mutation flows through mcp__polynoia__* —
        # audited (.polynoia/audit.jsonl), sandbox-bounded, gateable (ADR-005),
        # and role-filtered (ADR-013).
        role_tool_names = set(
            tools_for_role(tool_role, set(tools_whitelist or []) or None).keys()
        )
        effective_allowed = allowed_tools if allowed_tools else [
            f"mcp__polynoia__{name}" for name in sorted(role_tool_names)
        ]
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
        # Capture claude CLI stderr so it can be surfaced in error messages.
        # Default SDK behavior inherits stderr from the parent — useful when
        # tailing the server terminal, useless when the error needs to reach
        # the UI. We keep a per-session ring buffer AND echo to sys.stderr
        # so the original terminal-debug visibility is preserved for devs who
        # were watching the server log.
        import sys as _sys
        stderr_buf: list[str] = []

        def _capture_stderr(line: str) -> None:
            stderr_buf.append(line)
            if len(stderr_buf) > 200:
                del stderr_buf[: len(stderr_buf) - 200]
            try:
                _sys.stderr.write(line)
            except Exception:  # noqa: BLE001 — best-effort tee
                pass

        # On macOS the SDK's BUNDLED `claude` binary fails the Pro OAuth request
        # with a 403 ("Request not allowed") — it isn't the Keychain item's
        # trusted app and is an older pinned build. The host's SYSTEM `claude`
        # (which owns that credential + is current) works. So in direct-creds
        # mode (macOS desktop) point the SDK at the system claude; the Linux
        # server keeps the SDK's bundled binary (its copy-cred model works there).
        _cli_path = shutil.which("claude") if use_direct_host_credentials() else None

        opts = ClaudeAgentOptions(
            cwd=effective_cwd,
            system_prompt=sys_prompt_param,
            tools=[],                       # no built-ins — MCP tools only
            allowed_tools=effective_allowed,
            setting_sources=[],             # don't inherit parent skills/plugins
            strict_mcp_config=True,         # only the `polynoia` MCP server
            permission_mode="bypassPermissions",   # MCP boundary suffices
            model=model,
            **({"cli_path": _cli_path} if _cli_path else {}),
            env=sandbox_env,
            # 关键:开启流式 + include partial messages 让我们拿到 text-delta
            include_partial_messages=True,
            # Extended thinking ON with a healthy budget. With tools, recent
            # Claude models INTERLEAVE thinking between tool calls — so an
            # orchestrator that hits a tool error (or finishes a dispatch) emits
            # fresh, visible thinking before its next action instead of a silent
            # gap. Combined with the streaming reasoning ticker, the long pauses
            # users perceived as "卡" get filled with live activity.
            thinking={"type": "enabled", "budget_tokens": 8000},
            mcp_servers={"polynoia": polynoia_mcp},
            stderr=_capture_stderr,
        )
        return ClaudeCodeSession(
            opts=opts,
            agent_id=self.meta.agent_id,
            sandbox=sandbox,
            stderr_buf=stderr_buf,
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
    # Which block indices are `thinking` blocks → completed as ReasoningPayload
    # (not TextPayload). Streamed live so the UI can show "正在思考" + fold it.
    reasoning_blocks: set[int] = set()
    reasoning_start: dict[int, float] = {}   # block_idx → monotonic start (for "思考 N 秒")
    # tool_call_id → (message_id, part_id) so we can complete the same card
    # when the user message (tool_result) comes back from Claude Code.
    tool_call_part_id: dict[str, tuple[str, str]] = {}
    tool_call_payload: dict[str, ToolCallPayload] = {}
    # Live tool-ARGUMENT streaming: a tool_use block's input arrives as
    # input_json_delta fragments (partial JSON). We accumulate per block_idx and
    # re-emit the running card with a growing preview so the user watches the
    # args build (esp. a big `dispatch`) instead of staring at an empty card.
    block_tool_id: dict[int, str] = {}      # block_idx → tool_call_id
    tool_input_buf: dict[int, str] = {}     # block_idx → accumulated partial JSON
    tool_input_sent: dict[int, int] = {}    # block_idx → last-emitted length (throttle)
    tool_input_raw: dict[str, str] = {}     # tool_call_id → full raw partial JSON
    #   ^ kept so that when the model's args fail to parse (→ empty `input` +
    #     "X is a required property" error) we can still SHOW the raw JSON the
    #     model actually emitted on the error card.
    # Whether THIS assistant message streamed any reply text via StreamEvent.
    # Reset per message_start. When the upstream SSE is buffered (e.g. behind a
    # proxy that doesn't flush incrementally) NO StreamEvent arrives — only the
    # final AssistantMessage — and the text would be silently dropped. We use
    # this flag to emit the complete text once as a (non-streamed) fallback.
    streamed_text = False

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
                reasoning_blocks.clear()
                reasoning_start.clear()
                block_tool_id.clear()
                tool_input_buf.clear()
                tool_input_sent.clear()
                streamed_text = False
                tool_input_raw.clear()
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
                elif btype == "thinking":
                    # Stream the model's thinking like text (start → deltas →
                    # stop), but tagged reasoning so the UI folds it away.
                    part_id = _new_id()
                    block_to_part[idx] = part_id
                    block_text[idx] = ""
                    reasoning_blocks.add(idx)
                    reasoning_start[idx] = time.monotonic()
                    yield PartStartedEvent(
                        turn_id=turn_id,
                        task_id=task_id,
                        message_id=current_message_id or _new_id(),
                        part_id=part_id,
                        part=ReasoningPayload(body=[PNTextBlock(c="")]),
                    )
                elif btype == "tool_use":
                    # Emit a RUNNING tool card the instant the tool block opens
                    # — BEFORE Claude finishes generating the (potentially huge)
                    # arguments. Otherwise a big `dispatch` call shows nothing for
                    # the ~20-30s it takes to write the args, which reads as the
                    # system hanging. We reuse this (msg_id, part_id) when the
                    # final ToolUseBlock lands so the card updates in place.
                    tool_id = block.get("id") or _new_id()
                    tool_name = block.get("name", "tool")
                    tc_msg_id = _new_id()
                    tc_part_id = _new_id()
                    tool_call_part_id[tool_id] = (tc_msg_id, tc_part_id)
                    block_tool_id[idx] = tool_id
                    tool_input_buf[idx] = ""
                    tool_input_sent[idx] = 0
                    running = ToolCallPayload(
                        tool_call_id=tool_id,
                        name=tool_name,
                        input={},
                        state="running",
                        summary=_tool_summary(tool_name, {}),
                    )
                    tool_call_payload[tool_id] = running
                    yield PartCompletedEvent(
                        message_id=tc_msg_id,
                        part_id=tc_part_id,
                        part=running,
                    )
            elif ev_type == "content_block_delta":
                idx = ev.get("index", 0)
                delta = ev.get("delta", {})
                # text_delta carries `text`; thinking_delta carries `thinking`.
                # Both stream into the same part as {"text": ...} deltas.
                dtype = delta.get("type")
                if dtype in ("text_delta", "thinking_delta"):
                    existing_part_id = block_to_part.get(idx)
                    if existing_part_id:
                        chunk = (
                            delta.get("thinking", "")
                            if dtype == "thinking_delta"
                            else delta.get("text", "")
                        )
                        block_text[idx] = block_text.get(idx, "") + chunk
                        if dtype == "text_delta":
                            streamed_text = True
                        yield PartDeltaEvent(
                            message_id=current_message_id or _new_id(),
                            part_id=existing_part_id,
                            delta={"text": chunk},
                        )
                elif dtype == "input_json_delta":
                    # Tool ARGS streaming in (partial JSON). Accumulate + re-emit
                    # the running card with a growing preview, throttled to every
                    # ~64 new chars so a big `dispatch` shows its args building
                    # instead of a frozen empty card. Final input lands via the
                    # AssistantMessage ToolUseBlock (which replaces the preview).
                    tool_id = block_tool_id.get(idx)
                    if tool_id:
                        tool_input_buf[idx] = tool_input_buf.get(idx, "") + (
                            delta.get("partial_json", "") or ""
                        )
                        buf = tool_input_buf[idx]
                        tool_input_raw[tool_id] = buf  # full raw, every delta
                        if len(buf) - tool_input_sent.get(idx, 0) >= 64:
                            tool_input_sent[idx] = len(buf)
                            prev = tool_call_payload.get(tool_id)
                            meta = tool_call_part_id.get(tool_id)
                            if prev is not None and meta is not None:
                                # Stream the raw args into the EXPANDABLE body
                                # (input_preview), not the one-line summary, so
                                # the user can open the fold and watch them build.
                                # WINDOW the preview so the live write card keeps
                                # STREAMING (WriteStreamCard auto-scrolls to the
                                # newest content) while staying parseable AND bounded:
                                #  · HEAD keeps the ``{"path":...,"content":"`` anchor —
                                #    the frontend's extractWriteFields is HEAD-anchored
                                #    (matches the literal ``"content":"`` near the START);
                                #    drop it and content won't parse → the card freezes
                                #    at "准备写入…".
                                #  · TAIL keeps the lines being written RIGHT NOW so the
                                #    card shows live progress instead of a frozen prefix.
                                # Re-pushing a fixed-size window each tick is O(N), not
                                # O(N²); the full input lands on completion anyway.
                                if len(buf) <= 4000:
                                    preview = buf
                                else:
                                    head = buf[:300].rstrip("\\")
                                    tail = buf[-3600:]
                                    # don't start the tail on a dangling escape half
                                    if tail[:1] in ('"', "\\"):
                                        tail = tail[1:]
                                    preview = head + "…" + tail
                                updated = prev.model_copy(update={
                                    "input_preview": preview,
                                    "summary": "生成参数中…",
                                })
                                tool_call_payload[tool_id] = updated
                                yield PartCompletedEvent(
                                    message_id=meta[0], part_id=meta[1], part=updated,
                                )
                # signature_delta (thinking signature): ignored — UI-irrelevant.
            elif ev_type == "content_block_stop":
                idx = ev.get("index", 0)
                existing_part_id = block_to_part.get(idx)
                if existing_part_id:
                    final_text = block_text.get(idx, "")
                    if idx in reasoning_blocks:
                        # Stamp how long the model thought so "思考 N 秒" persists
                        # + survives a refresh (the live client timer is gone then).
                        secs = _reasoning_seconds(reasoning_start.get(idx))
                        part: TextPayload | ReasoningPayload = ReasoningPayload(
                            body=[PNTextBlock(c=final_text)], seconds=secs,
                        )
                    else:
                        part = TextPayload(body=[PNTextBlock(c=final_text)])
                    yield PartCompletedEvent(
                        message_id=current_message_id or _new_id(),
                        part_id=existing_part_id,
                        part=part,
                    )
            # message_delta / message_stop: 不发(等 ResultMessage)
            continue

        # ── AssistantMessage: 最终聚合 ────
        # tool_use 块在这里来(stream_event 里我们没发过 tool part)
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    # 通用 tool-call 卡,state=running(等 tool_result 来再改 completed)
                    # REUSE the (msg_id, part_id) we may have already emitted at
                    # content_block_start (the running card shown during arg
                    # generation) so the card updates IN PLACE with the full
                    # input instead of spawning a duplicate. Each tool keeps its
                    # own message_id so concurrent tools don't overwrite in store.
                    meta = tool_call_part_id.get(block.id)
                    if meta is not None:
                        tc_msg_id, part_id = meta
                    else:
                        tc_msg_id = _new_id()
                        part_id = _new_id()
                        tool_call_part_id[block.id] = (tc_msg_id, part_id)
                    parsed_input = block.input or {}
                    # If the parsed input came back empty but the model DID stream
                    # raw JSON (e.g. malformed / bad-escape args that the SDK
                    # couldn't parse → empty dict + "X is a required property"
                    # error), keep the raw bytes as input_preview so the error
                    # card still SHOWS what the model tried to send.
                    preview: str | None = None
                    if not parsed_input:
                        raw = tool_input_raw.get(block.id, "")
                        if raw.strip():
                            preview = raw if len(raw) <= 4000 else ("…" + raw[-4000:])
                    payload = ToolCallPayload(
                        tool_call_id=block.id,
                        name=block.name,
                        input=parsed_input,
                        input_preview=preview,
                        state="running",
                        summary=_tool_summary(block.name, parsed_input),
                    )
                    tool_call_payload[block.id] = payload
                    yield PartCompletedEvent(
                        message_id=tc_msg_id,
                        part_id=part_id,
                        part=payload,
                    )
                elif isinstance(block, ThinkingBlock):
                    # Already streamed via StreamEvent (content_block_start/
                    # delta/stop, btype=="thinking") as a ReasoningPayload part.
                    # Skip here to avoid emitting it twice (same as TextBlock).
                    pass
                elif isinstance(block, TextBlock):
                    # Normally the text already streamed via StreamEvent → skip to
                    # avoid a duplicate. BUT if nothing streamed for this message
                    # (e.g. upstream SSE was buffered behind a proxy → only the
                    # final AssistantMessage arrived), the reply would be silently
                    # dropped. Emit the complete text once as a non-streamed
                    # fallback. Guarded by `streamed_text`, so the normal streaming
                    # path is unaffected (no double-emit).
                    if not streamed_text and (block.text or "").strip():
                        yield PartCompletedEvent(
                            message_id=current_message_id or _new_id(),
                            part_id=_new_id(),
                            part=TextPayload(body=[PNTextBlock(c=block.text)]),
                        )
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
                # `error_during_execution` = the SDK client/session is broken
                # (half-aborted from a prior cancel, cwd vanished, etc.), NOT a
                # content/API failure. Raise instead of yielding a dead-end
                # TurnFailedEvent so the WS layer evicts the session + respawns
                # a fresh one and retries. (api_error_status is unset here.)
                if msg.subtype == "error_during_execution" and not getattr(
                    msg, "api_error_status", None
                ):
                    raise RuntimeError("claude session error_during_execution — respawn")
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
                    # Fall back: include the underlying SDK error list if any.
                    # The CLI also surfaces a human message in `result` for
                    # pre-flight failures that have no api_error_status / errors
                    # — e.g. an unauthenticated CLI returns
                    # ``is_error=True, subtype="success", result="Not logged in
                    # · Please run /login"``. Without checking `result` the user
                    # sees the useless "agent turn failed (no further detail)"
                    # AND we miss the "logged in" / "/login" rate markers that
                    # would route this to the retryable-credential path.
                    errors = getattr(msg, "errors", None) or []
                    result_text = (getattr(msg, "result", None) or "").strip()
                    if errors:
                        message = "; ".join(str(e) for e in errors)
                    elif result_text:
                        message = result_text
                    else:
                        message = "agent turn failed (no further detail)"
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
        stderr_buf: list[str] | None = None,
    ):
        self.session_id = _new_id()
        self.agent_id = agent_id
        self._opts = opts
        self._sandbox = sandbox
        self._cleanup_on_close = cleanup_on_close
        self._client: ClaudeSDKClient | None = None
        self._lock = asyncio.Lock()  # serialize send() calls per session
        # Tail of claude CLI stderr — populated by the stderr callback wired
        # up in start_session. Used to enrich exceptions so the UI/log shows
        # the actual CLI complaint instead of the SDK's "Check stderr" stub.
        self._stderr_buf = stderr_buf if stderr_buf is not None else []

    def _stderr_tail(self, max_chars: int = 800) -> str:
        if not self._stderr_buf:
            return ""
        joined = "".join(self._stderr_buf).strip()
        if len(joined) > max_chars:
            return "…" + joined[-max_chars:]
        return joined

    async def _ensure_client(self) -> ClaudeSDKClient:
        if self._client is None:
            client = ClaudeSDKClient(options=self._opts)
            # If connect() raises, the SDK internally calls disconnect() which
            # leaves the object alive but with _transport=None. Caching that
            # half-dead instance is fatal: every later query() hits the
            # "Not connected. Call connect() first." guard, masking the real
            # connect-time error forever. Only commit to self._client AFTER
            # connect succeeds.
            try:
                await client.connect()
            except BaseException as exc:
                self._client = None
                tail = self._stderr_tail()
                if tail and isinstance(exc, Exception):
                    # SDK's ProcessError says "Check stderr output for details"
                    # but never actually surfaces stderr. Wrap so the real CLI
                    # output reaches the UI error chunk.
                    raise RuntimeError(
                        f"{type(exc).__name__}: {exc} | claude stderr: {tail}"
                    ) from exc
                raise
            self._client = client
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

            # Send the user query. With image attachments, send a STRUCTURED user
            # message (text block + base64 image blocks) so the model actually
            # SEES the pixels — `client.query(str)` would only carry text. Each
            # attachment is {media_type, data(base64), name}; non-image atts are
            # ignored here (only vision is wired).
            images = [
                a
                for a in (attachments or [])
                if a.get("data") and str(a.get("media_type", "")).startswith("image/")
            ]
            if images:
                content: list[dict[str, Any]] = [{"type": "text", "text": text}]
                for a in images:
                    content.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": a["media_type"],
                                "data": a["data"],
                            },
                        }
                    )

                async def _structured_msg() -> AsyncIterator[dict[str, Any]]:
                    yield {
                        "type": "user",
                        "message": {"role": "user", "content": content},
                        "parent_tool_use_id": None,
                    }

                await client.query(_structured_msg())
            else:
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
