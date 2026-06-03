# 图示:文件展示流(编排者统一展示 · Option B)

> 场景:多 agent burst 中,worker 在自己的隔离分支产出文件;用户要求「展示的成果卡必须源自
> main、且由协调者统一交付」。本图说明 worker 的 `present` 如何被服务端延迟(defer),文件如何
> 随 burst 合并进 main,以及编排者如何在合并后的汇总轮从 main 统一 `present`。
>
> 决策依据:同类项目调研(Cursor 云端 agent = 分支产物→PR→受控合并;无人「展示时即时合并」)。
> 实现:`/api/present` gate(读 `_conv_bursts` 现有键,不改结构) + `_PresentTool` 回传 deferred +
> is_last 汇总 nudge 的 `_present_clause`。源自 main 由 `_resolve_present_path`(main 优先)保证。

## GPT-IMAGE-2 prompt

```
A clean, technical infographic in modern flat-design style on a soft off-white
(#f6f2ea) background. Title at top in bold sans-serif:
"Polynoia — Orchestrator-Presents File Flow".

Horizontal left-to-right flow in three labeled stages, each a rounded panel.

STAGE 1 (left panel, header "① Burst running — workers isolated"):
Three small gray rounded cards stacked, labeled "worktree: ag-A (branch)",
"worktree: ag-B (branch)", "worktree: ag-C (branch)". From card ag-A, an orange
arrow labeled "present(report.docx)" points right to a blue rounded box
"POST /api/present". Inside that blue box, a warm-orange diamond gate labeled
"active burst?  sender != orchestrator?". A red arrow exits the gate downward to
a red-outlined pill "DEFERRED — card suppressed; note: use report()". A small
green tag on ag-A card reads "file committed to branch ✓ (rides the merge)".

STAGE 2 (center panel, header "② All workers done — single controlled merge"):
A warm-orange rounded box "_merge_burst_to_main()" with three thin gray arrows
feeding into it from the three branch cards of Stage 1. One green arrow exits
right into a green rounded cylinder labeled "main (workspace ROOT, single HEAD)"
containing file chips "report.docx", "data.csv", "index.html". Below, a small
gray note: "burst popped from registry → gate now clear".

STAGE 3 (right panel, header "③ Summary turn — coordinator presents from main"):
A blue rounded card "Orchestrator summary turn (fresh worktree synced to main)".
An orange arrow labeled "present(report.docx)" goes to the SAME blue box
"POST /api/present"; this time the orange gate diamond shows a green check
"no active burst → pass". A green arrow exits to a gray chat bubble card
"file card — from main, attributed to 林知夏 (coordinator)" which points to a
simple user avatar on the far right labeled "User sees canonical deliverable".

Bottom caption strip in dark slate (#1F2937): "_resolve_present_path: main first,
worktree fallback only pre-merge. Workers never surface unmerged branch bytes."

Color palette: off-white bg #f6f2ea, soft blue #5B8FF9 for system/server boxes,
warm orange #F2994A for tool calls & the gate, gray #E5E7EB for messages/worktrees,
fresh green #27AE60 for main/success/merge, red #E04A4A for deferred/miss, dark
slate #1F2937 for text. Thin 1-2px strokes, no 3D, no shadows except the title.

Aspect ratio: 16:9.
```
