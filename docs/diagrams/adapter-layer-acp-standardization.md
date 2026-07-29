# Adapter 层:自研 PAP 适配 vs. 用现成 ACP 库标准化(对比图)

**场景**:评估"把 3 个手写 adapter 换成现成 ACP 库/桥"的收益边界。结论是分层的:协议管道(~37%)可由官方 `agent-client-protocol`(Python SDK)+ 现成桥替换;Polynoia 业务逻辑(~63%,沙箱/角色 MCP/工作区/卡片/编排)不可替换。Claude 不走 claude-code-acp(那层本就建在我们直连的 claude_agent_sdk 之上)。

> 渲染规范见 `CLAUDE.md §12`。蓝=ACP/system,橙=tools/MCP,灰=messages,绿=可复用/省下的,红=自研不可替换/风险。

## 2026-07 实施状态

- Tier 1 已完成:OpenCode 已改用官方 Python ACP SDK。
- Tier 2 已完成低风险部分:`GenericAcpAdapter` 统一负责 ACP 建连、会话生命周期、PAP 事件翻译、MCP 注入、超时/取消和进程恢复;`acp_providers.py` 以声明式记录管理命令、模型配置和 provider 差异。
- OpenCode 已迁移到通用层,其专用数据目录、权限配置和尾部消息宽限仍作为薄 provider shim 保留。
- Claude Code 和 Codex 暂不迁移。它们依赖各自协议中的 hooks、权限或 app-server 能力,当前没有足够收益支撑迁移风险。
- 新增标准 ACP Agent 时,后端运行时通常只需增加一条 `AcpProvider` 声明;产品侧的 onboarding、联系人模板和模型提示仍需单独配置。

下方 GPT-IMAGE-2 prompt 保留为方案评估时的历史设计稿,其中“退役大部分 codex.py”不是当前实施结论。

## GPT-IMAGE-2 prompt

```
A clean, technical infographic in modern flat-design style on a soft off-white
background. Title at top in bold sans-serif: "Polynoia adapter layer — hand-rolled today vs. ACP-standardized".

Two side-by-side panels separated by a thin vertical divider. Left panel header "TODAY — 3 bespoke adapters (~3220 LOC)"; right panel header "PROPOSED — 1 generic ACP client + ready-made bridges".

LEFT PANEL. Three stacked horizontal lanes, each a rounded rectangle:
  - Lane 1 gray, label "claude_code.py  (776 LOC)", sub-label "imports @anthropic claude_agent_sdk (Python) directly".
  - Lane 2 gray, label "opencode.py  (827 LOC)", sub-label "HAND-ROLLED ACP JSON-RPC client (no library)" — put a small red badge "reinvents published SDK".
  - Lane 3 gray, label "codex.py  (1166 LOC)", sub-label "hand-rolled app-server JSON-RPC + exec JSONL fallback" — small red badge "biggest & messiest".
  Below the three lanes a full-width green strip labeled "shared: base.py PAP / AdapterEvent · pool.py · adapter_to_chunk.py".
  A thin caption under the panel: "~37% (≈1200 LOC) transport plumbing  +  ~63% (≈2000 LOC) Polynoia logic".

RIGHT PANEL. At the top one blue rounded box "AcpAdapter (one file)  — uses official `agent-client-protocol` (PyPI, Python ACP SDK)". From it, three thin arrows fan down to three small orange "bridge process" pills:
  - "opencode acp  (built-in)"
  - "zed-industries/codex-acp  (Rust, official, 793★)"
  - "future: gemini CLI acp" drawn dashed.
  Beside the AcpAdapter box, a separate blue box kept OUT of the ACP fan, labeled "claude_code.py stays on direct claude_agent_sdk" with a small green note "keeps hooks · permission modes · sub-agents · role-MCP control".
  Below, the SAME full-width green strip "shared: base.py PAP / AdapterEvent · pool.py · adapter_to_chunk.py  (UNCHANGED)".
  A red callout box at the bottom-right: "cost of bridges: extra Node/Rust runtime dep + IPC hop + version-drift / supply-chain surface".

CENTER, straddling the divider, a horizontal green banner: "IRREDUCIBLE — no library provides this: sandbox/worktree routing · role-based MCP injection + pending-edit gate · workspace read-only mounting · MessagePayload 12-kind cards · orchestrator task decomposition · R1/R2 context isolation · burst lanes".

Bottom strip full width, soft blue tint, three short verdicts in a row:
  "Tier 1 (low risk): swap opencode hand-roll → Python ACP SDK"  |
  "Tier 2 (the bet): 3 adapters → 1 AcpAdapter + bridge configs; retire most of codex.py"  |
  "Tier 3 (don't): route Claude through claude-code-acp".

Color palette + style: off-white bg, soft blue #5B8FF9 for ACP/system, warm orange #F2994A for tools/MCP/bridges, gray #E5E7EB for current adapters/messages, fresh green #27AE60 for reusable/saved & irreducible-moat, red #E74C3C for risk/anti-pattern, dark slate #1F2937 for text. Thin 1-2px strokes, no 3D, no shadows except title. Monospace font for filenames and package names.

Aspect ratio: 16:9.
```
