# 图示索引

> 每个 `.md` 含一张关键过程/架构边界的结构化图示(GPT-IMAGE-2 prompt + 场景说明)。规范见 `/CLAUDE.md` §12。

| 图 | 主题 |
|---|---|
| [polynoia-multi-agent-runtime](polynoia-multi-agent-runtime.md) | 多 Agent 运行时:orchestrator 派活 → burst 泳道 → merge |
| [polynoia-data-schema](polynoia-data-schema.md) | 数据模型(Provider/Agent/Conversation/Message.parts) |
| [polynoia-three-platforms](polynoia-three-platforms.md) | 三端:Web / 桌面 Tauri / 移动 Capacitor 复用同一 web 构建 |
| [agent-context-composition](agent-context-composition.md) | 五层上下文 assembler 的组装与预算 |
| [agent-adapter-mechanics](agent-adapter-mechanics.md) | Adapter session 生命周期(spawn→connect→turn→resume→close) |
| [adapter-layer-acp-standardization](adapter-layer-acp-standardization.md) | Adapter 层把异构 CLI 协议标准化为 AdapterEvent |
| [claude-code-acp-bridge](claude-code-acp-bridge.md) | Claude Code ↔ 平台的桥接 |
| [codex-app-server-protocol](codex-app-server-protocol.md) | Codex `app-server` JSON-RPC 流式协议(见 ADR-021) |
| [present-flow-orchestrator](present-flow-orchestrator.md) | present 产物交付流 |
| [reasoning-and-execution-decoupling](reasoning-and-execution-decoupling.md) | 思考与执行解耦 |
| [agent-self-evolution](agent-self-evolution.md) | Agent 自演进 / 记忆 |
| [chat-ui-redesign](chat-ui-redesign.md) | 聊天 UI 重设计 |
