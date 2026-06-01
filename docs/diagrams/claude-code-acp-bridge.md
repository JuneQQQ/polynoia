# `@zed-industries/claude-code-acp` — ACP↔Claude Agent SDK 桥接结构图

**场景**:解释 Zed 官方的 `claude-code-acp` 适配器原理 —— 它把 Claude Code(经 `@anthropic-ai/claude-agent-sdk`)包装成一个 **ACP agent**,让任意 ACP 客户端(Zed 等)能像驱动 OpenCode 一样驱动 Claude Code。是我们 `OpenCodeAdapter`(消费 ACP 的 client 侧)的**镜像对侧**(提供 ACP 的 agent 侧)。

> 渲染规范见 `CLAUDE.md §12`。颜色编码:蓝=ACP/system,橙=tools/permission,灰=messages,绿=success/cache,红=error。

## GPT-IMAGE-2 prompt

```
A clean, technical infographic in modern flat-design style on a soft off-white
background. Title at top in bold sans-serif: "claude-code-acp — bridging ACP clients to the Claude Agent SDK".

Three vertical columns connected by horizontal arrows, left to right.

LEFT column, header "ACP CLIENT (e.g. Zed editor)" in soft blue #5B8FF9.
Below it a rounded panel listing: "JSON-RPC over NDJSON", "session/new · session/prompt · session/cancel", "session/update notifications (inbound)", "requestPermission (inbound)", "fs.readTextFile / writeTextFile (inbound)", "terminal (inbound)". A thin label under the panel: "owns the files, the diff review UI, the terminal".

MIDDLE column, header "claude-code-acp  (this package)" in dark slate #1F2937, drawn as the central bridge box with a subtle green #27AE60 outline. Inside, stacked sub-boxes top to bottom:
  - blue box "AgentSideConnection + ndJsonStream  (stdin=in, stdout=out)" with a small note "console.log → stderr (stdout is protocol-only)".
  - dark box "class ClaudeAcpAgent  implements ACP Agent" listing methods: "initialize · newSession · loadSession · prompt · cancel · setSessionMode".
  - a gray box "prompt() pump loop" with text "for await (msg of query) → translate → client.sessionUpdate(...)".
  - two translation chips side by side in gray #E5E7EB: left chip "promptToClaude()  ACP content → Claude message  (text, @-mention resource → <context>, image base64/url, /mcp slash rewrite)"; right chip "toAcpNotifications()  Claude stream → ACP chunks  (text→agent_message_chunk, thinking→agent_thought_chunk, tool_use→tool_call pending, tool_result→tool_call_update completed/failed, TodoWrite→plan)".
  - an orange #F2994A box "canUseTool() → client.requestPermission()  modes: default / acceptEdits / plan / dontAsk / bypassPermissions".
  - an orange box "in-process 'acp' MCP server  (type: sdk)  re-registers mcp__acp__Read / Write / Edit / Bash" with an arrow curving back to the LEFT column labeled in green "file I/O & terminal proxied back to the client" and a small red note "native Read/Write/Edit/Bash DISABLED via disallowedTools".

RIGHT column, header "CLAUDE AGENT SDK  @anthropic-ai/claude-agent-sdk" in soft blue #5B8FF9.
Below it a rounded panel: "query({ prompt, options })", "spawns claude-code CLI subprocess", "systemPrompt preset: claude_code", "settingSources: user/project/local", "PreToolUse / PostToolUse hooks", "emits: system · stream_event · assistant · user · result". A thin label: "the real model loop + tool execution".

Horizontal arrows: LEFT →(prompt)→ MIDDLE →(query input)→ RIGHT, and RIGHT →(SDK messages)→ MIDDLE →(session/update)→ LEFT. Make the return arrows a slightly lighter shade.

Bottom strip, full width, soft green #27AE60 tint, single line: "Dependency triad:  @agentclientprotocol/sdk  +  @anthropic-ai/claude-agent-sdk  +  @modelcontextprotocol/sdk".

Color palette + style: off-white bg, soft blue #5B8FF9 for ACP/system, warm orange #F2994A for tools & permission, gray #E5E7EB for messages, fresh green #27AE60 for success/proxy region, red #E74C3C for disabled/error, dark slate #1F2937 for text. Thin 1-2px strokes, no 3D, no shadows except the title. Monospace font for all code-like labels (session/prompt, mcp__acp__Read, etc.).

Aspect ratio: 16:9.
```
