# A2A Bridge Phase 1 SDK/TCK Flow

场景：用于评审第一阶段的 SDK 公开扩展边界、多 Agent 路由隔离，以及生产装配与测试专用 TCK executor 之间的隔离关系。

## GPT Image 2 Prompt

```text
A clean, highly legible technical infographic in modern flat-design style on a
soft off-white background. 16:9 landscape. Title centered at top in bold
sans-serif: "Polynoia A2A Bridge — Phase 1 SDK/TCK Feasibility".

Use a left-to-right main protocol flow occupying the upper two-thirds:

1. Far-left pale gray client box labeled exactly:
   "A2A Client"
   Sub-labels:
   "Agent Card GET"
   "A2A v1 JSON-RPC"

2. A blue outer container labeled:
   "Polynoia A2A Bridge"
   Inside it, show three vertically stacked route lanes:
   "Agent alpha"
   "Agent beta"
   "Agent tck"
   Each lane has two small endpoint pills:
   "/.well-known/agent-card.json"
   "/a2a + /a2a/"
   Add a small annotation above the lanes:
   "One handler + one TaskStore per Agent"

3. Inside each route lane, show the same four blue components connected by
   thin arrows, with these exact labels:
   "RedactingServerCallContextBuilder"
   "StrictRequestContextBuilder"
   "DefaultRequestHandlerV2"
   "InMemoryTaskStore (Phase 1 only)"
   Put a green shield badge beside the first builder labeled:
   "No Authorization / Cookie retention"
   Put a green validation badge beside the second builder labeled:
   "Context inference · media validation · terminal-task guard"

4. To the right, a blue SDK-owned processing box labeled:
   "Official a2a-sdk 1.1.2"
   Inside it list exactly:
   "JSON-RPC parsing"
   "ActiveTask producer / consumer"
   "Task operations"
   "Error serialization"
   "Lifecycle streaming"
   Add a small lock label:
   "Pinned public seams"

Across the bottom third, draw a clearly separated warm-orange testing lane
with a dashed border and title:
"TEST-ONLY TCK VOCABULARY — never imported by production src/"

Inside the testing lane, place these boxes from left to right:
"Pinned A2A TCK"
Sub-label: "commit 5996b79f...e49e"
Arrow to:
"tests/tck_app.py"
Arrow to:
"tests/tck_executor.py"
Arrow to a green report box:
"JSON-RPC MUST Report"

Inside the executor box list compact exact labels:
"messageId prefix dispatch"
"Direct Message"
"Text / raw / URL / data artifacts"
"Chunked artifact"
"input-required"
"Long-running resubscribe task"

Draw a red vertical firewall line between the orange testing lane and any
future production connector area. Label the firewall exactly:
"Boundary: TCK behavior cannot enter HttpJsonConnector"

Add a small future-phase note in light gray at bottom-right:
"Phase 3 replaces InMemoryTaskStore with BoundedInMemoryTaskStore"

Color palette: soft blue #5B8FF9 for SDK/server components, warm orange
#F2994A for test-only components, gray #E5E7EB for clients and future work,
fresh green #27AE60 for validation/pass evidence, red #D64545 for isolation
boundaries, dark slate #1F2937 for text. Thin 1–2 px strokes, rounded 8 px
corners, no 3D, minimal shadows, generous whitespace, exact English labels,
small monospace font only for class names and commit hash.
```
