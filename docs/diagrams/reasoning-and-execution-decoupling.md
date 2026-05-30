# 思考块流式 + 执行解耦(刷新安全)— 图示 prompt

> 场景:本轮新增「模型思考块流式展示 + 折叠」与「执行与连接解耦(后端主导、刷新不中断、Agent 级终止)」两大改动。按 CLAUDE.md §12 规范,跨人对齐 mental model 需要结构化图示。以下两张图分别讲清「思考块三层协议数据流」和「执行解耦 + 单泳道终止生命周期」。标签全英文,技术词原样保留。

---

## 图 1 — Reasoning(thinking)三层协议数据流

**一句话场景:** 模型的 thinking 如何从三种 CLI 适配器,经 PAP → AI SDK chunk 两层翻译,流到前端折叠展示,且**不污染回复正文 / 不进上下文**。

```
A clean, technical infographic in modern flat-design style on a soft off-white
(#FAF8F4) background. Title at top in bold sans-serif:
"Polynoia · Reasoning (thinking) flow across three protocol layers".

Horizontal left-to-right flow in FOUR labeled column-bands, each a rounded
rectangle with a thin 1.5px stroke, connected by arrows.

BAND 1 — "Adapter (PAP source)", soft blue #5B8FF9 header. Inside, three small
stacked chips, each showing how that CLI emits thinking:
  · "Claude Code: StreamEvent content_block(type=thinking) → thinking_delta"
  · "Codex: item.started/updated/completed (type=reasoning), cumulative text → suffix delta"
  · "OpenCode: session/update agent_thought_chunk"
A short note under the band: "all three normalize to PartStarted/Delta/Completed(ReasoningPayload)".

BAND 2 — "PAP AdapterEvent", warm orange #F2994A header. Three small pills in a
vertical sequence with downward arrows:
  "PartStartedEvent(ReasoningPayload)" → "PartDeltaEvent {text: …}" →
  "PartCompletedEvent(ReasoningPayload, body=[final])".
A dashed callout box to the side in muted gray: "kept OUT of the reply buffer
(_tap_text_into) — thinking is NOT the reply, NOT scanned for @mentions".

BAND 3 — "AI SDK 6 UIMessageChunk", soft blue #5B8FF9 header. Three pills mirroring
text-* but tinted a DESATURATED lavender-gray to signal 'de-emphasized':
  "reasoning-start {id, sender}" → "reasoning-delta {delta}" → "reasoning-end {id}".
Small note: "routed by open_reasoning_parts set; text-* vs reasoning-* never mix".

BAND 4 — "Frontend (React)", fresh green #27AE60 header. Show a small chat bubble
mockup containing a COLLAPSED thin strip labeled "▸ 思考过程 (folded)" in light
gray, with a tiny brain icon. Above it a faded ghosted state labeled
"while streaming: auto-EXPANDED · 正在思考… (spinner)". A curved arrow loops from
'streaming/expanded' down to 'done/collapsed' labeled "auto-collapse on
reasoning-end; click chevron to re-expand".

BOTTOM RAIL — a separate thin gray strip spanning all four bands titled
"Persistence & context": two boxes —
  green box "Persisted as ReasoningPayload row → folded thinking survives refresh"
  red-outlined box "EXCLUDED from L4 history / ledger (_format_message_body → \"\") —
  no context bloat, no cross-agent thinking leak".

Color palette: off-white bg, soft blue #5B8FF9 for protocol/system layers, warm
orange #F2994A for PAP events, desaturated lavender-gray for reasoning chunks
(de-emphasis), fresh green #27AE60 for UI/persist-success, red #E5484D for the
exclusion marker, dark slate #1F2937 for text. Thin 1–2px strokes, no 3D, no
shadows except the title. Monospace font for all technical tokens
(thinking_delta / ReasoningPayload / reasoning-delta / _format_message_body).

Aspect ratio: 16:9.
```

---

## 图 2 — 执行解耦 + Agent 级终止生命周期(刷新安全)

**一句话场景:** 为什么关浏览器 / 刷新不会中断后台执行,以及单条泳道「终止」如何只杀一个 worker 而让整个 burst 仍跑完(含 orchestrator 收尾)。核心:会话级(模块级 per-conv)任务状态 + 广播式 emit + 只有显式 abort 才取消。

```
A clean, technical infographic in modern flat-design style on a soft off-white
(#FAF8F4) background. Title at top in bold sans-serif:
"Polynoia · Backend-led execution: refresh-safe + per-lane terminate".

CENTER — a large rounded rectangle labeled "MODULE-LEVEL per-conv state (survives
any single WS connection)", soft blue #5B8FF9. Inside it, five small labeled
boxes in a row:
  "_conv_inflight {set of live Tasks}" (green accent — strong refs, no GC),
  "_conv_agent_tasks {agent_id → Task}" (abort/status handle),
  "_conv_agent_locks {agent_id → Lock}",
  "_conv_bursts {tp_id → registry}",
  "_conv_dispatchers {in-flight dispatcher Tasks}".
Caption under it: "_spawn_turn() registers strong ref + by-id slot;
done-callback → _maybe_prune_conv() frees only when fully idle + unattached".

LEFT — two stacked browser chips in gray #E5E7EB: "Tab A (WS conn 1)" and
"Tab B (WS conn 2)". A dashed red arrow from Tab A labeled "refresh / close"
with a bold annotation: "disconnect does NOT cancel tasks — finally only
unregisters this conn's outbox". An arrow from each tab to a shared box
"_conv_outboxes {conn queues}", and from there a green broadcast arrow
"emit() → _broadcast_to_conv(): every live tab re-attaches to the stream".

RIGHT — a burst lane diagram: orchestrator avatar (purple) dispatches 3 worker
lanes (gray columns) "顾屿 / 沈昭 / 苏念". One lane has a red "■ Stop (abort
agent_id)" button. Show the cancel path as a red arrow into that lane labeled
"CancelledError → _mark_burst_task(lane, FAILED) BEFORE re-raise". The other two
lanes show green "done". Below all three, a green merge arrow into a single box
"is_last → merge to main + orchestrator summary turn" with a check mark,
annotated "aborting ONE lane still completes the burst".

BOTTOM RAIL — three small guard chips in a row (lessons from the adversarial
review), each a thin red-outlined box:
  "strong-ref set prevents Task GC on agent_id overwrite",
  "is_dispatcher gate: only the orchestrator's own aborted turn clears
  _pending_dispatches (no zombie revive, no sibling wipe)",
  "sender_loop catches WebSocketDisconnect + unregisters queue (no unbounded
  growth on half-closed socket)".

Color palette: off-white bg, soft blue #5B8FF9 for module-level state, warm
orange #F2994A for the dispatcher/spawn machinery, gray #E5E7EB for connections/
tabs, fresh green #27AE60 for broadcast + success + merge, red #E5484D for
disconnect/abort/guard markers, purple #8B5CF6 for orchestrator, dark slate
#1F2937 for text. Thin 1–2px strokes, no 3D, no shadows except the title.
Monospace for all tokens (_conv_inflight / _spawn_turn / _mark_burst_task /
is_dispatcher / _broadcast_to_conv).

Aspect ratio: 16:9.
```

---

## 渲染说明

- 两张图都遵守 §12.2 颜色编码(蓝=system/protocol、橙=tools/PAP、灰=messages/connections、绿=success/cache/broadcast、红=error/abort/guard),reasoning chunk 额外用**去饱和淡紫灰**表达「淡化」语义。
- 渲染图(可选)放本目录 `pic/`,文件名 `reasoning-flow.png` / `execution-decoupling.png`。
- 关联代码:`adapters/{claude_code,codex,opencode}.py`、`transport/{adapter_to_chunk,ui_message_chunk}.py`、`api/routes.py`(`_spawn_turn` / `_maybe_prune_conv` / `_mark_burst_task` / `is_dispatcher`)、`context/ledger.py`(reasoning 排除)、前端 `parts/ReasoningPart.tsx`。
