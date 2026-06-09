# Codex `app-server` 流式协议时序图

> 场景:解释 CodexAdapter 为什么/怎么从 `codex exec --json`(整段非流式)切到
> `codex app-server`(token 级流式)的 JSON-RPC v2 长连接。配 [ADR-021](../ADR/ADR-021-codex-app-server-streaming.md)。
> 答辩素材:跨人对齐 "Codex 这条 adapter 的 turn 生命周期 + 三层协议映射"。

## 渲染图

(待渲染 — GPT-IMAGE-2)

## Prompt

```
A clean, technical sequence/flow infographic in modern flat-design style on a
soft off-white (#FAF9F6) background. Title at top in bold sans-serif:
"Codex app-server — token-streaming over JSON-RPC v2".
Subtitle, smaller gray: "Polynoia CodexAdapter · replaces exec --json (whole-message, non-streaming)".

LAYOUT: three vertical lanes (swimlanes) with labeled header boxes at top:
LEFT lane "Polynoia CodexSession" (soft blue #5B8FF9 header),
MIDDLE lane "codex app-server  (stdin/stdout, newline-delimited JSON-RPC 2.0)" (dark slate #1F2937 header),
RIGHT lane "PAP AdapterEvents → UI" (fresh green #27AE60 header).
Time flows top to bottom. Arrows are thin 1-2px, horizontal between lanes.

TOP CONTRAST BANNER (full width, two small pills):
- red pill "OLD: codex exec --json → 1× item.completed/agent_message  (whole block, no token deltas)"
- green pill "NEW: app-server → 24× item/agentMessage/delta  (real token stream)"

SEQUENCE (left→middle request arrows in blue, middle→left response/notification arrows in slate/orange):

1. blue arrow L→M: "initialize { clientInfo, capabilities:{ experimentalApi:true } }"
2. slate arrow M→L: "result { userAgent, codexHome }"
3. blue arrow L→M (thin, dashed): "initialized   (notification)"
4. blue arrow L→M: "thread/start { cwd, model? }"
5. slate arrow M→L: "result { thread:{ id }, model, modelProvider }"  — annotate "threadId captured"

--- divider line labeled "per turn (send)" ---

6. blue arrow L→M: "turn/start { threadId, input:[{type:text,text}],
   approvalPolicy:'never', sandboxPolicy:{type:'dangerFullAccess'} }"
7. slate arrow M→L (immediate): "result { turn:{ id, status:'inProgress' } }"  — annotate "+0.00s, non-blocking → turnId"
8. orange notification arrows M→L, stacked, labeled "stream":
   - "turn/started"
   - "item/started { agentMessage, id, text:'' }"          → RIGHT: green "PartStartedEvent(Text)"
   - "item/agentMessage/delta { itemId, delta:'The' }"  ×N  → RIGHT: green "PartDeltaEvent({text})  ← TOKENS"
   - "item/completed { agentMessage, text:'<full>' }"      → RIGHT: green "PartCompletedEvent(Text)"
   - "item/started/completed { commandExecution, command, aggregatedOutput, exitCode }" → RIGHT: green "ToolCallPayload (running→completed/error)"
   - "thread/tokenUsage/updated { tokenUsage.total }"      → annotate gray "accumulate usage"
   - "turn/completed { turn.status:'completed' }"          → RIGHT: green "TurnCompletedEvent(usage)"

9. bottom-left small box, dashed red border: "cancel: turn/interrupt { threadId, turnId }"
10. bottom-middle small box, gray: "credentials/MCP unchanged — app-server reads the same
    CODEX_HOME/config.toml ([mcp_servers.polynoia] injected by start_session)"

COLOR KEY (small legend, bottom-right): blue=client→server request, slate=server response,
orange=server→client notification (the stream), green=PAP event / success, red=cancel/old-path.
Monospace font for all JSON-RPC method names and field names (keep exact text like
`item/agentMessage/delta`, `sandboxPolicy:{type:'dangerFullAccess'}`, `experimentalApi:true`).
Thin strokes, no 3D, no drop shadows except under the title. Aspect ratio 16:9.
```
