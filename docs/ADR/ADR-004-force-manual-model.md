# ADR-004 — Adapter 无 listing API 时,强制手输模型 ID

- **状态**:accepted
- **日期**:2026-05-28
- **相关**:`apps/server/polynoia/api/agent_templates.py`(`ADAPTER_MODELS`),NewContactModal.tsx

## 背景

新建联系人选 adapter 时,UI 给个 model dropdown。问题:

- **Claude Code CLI** 不提供 `models list` 这种子命令,也没列表 API
- **OpenCode** 模型清单完全由用户本地 `~/.config/opencode/opencode.json` 决定(订阅计划 + 自加 provider 各种排列组合)
- **Codex** 走 OpenAI Responses API + 用户的 `~/.codex/config.toml` 自定义 provider

中央化预设清单的问题:
- Polynoia 拿不到真实数据 → 列表很容易**错**(显示一个用户实际没订阅的模型)
- 模型 ID 频繁变(`claude-opus-4-7` 这种)
- 用户的代理 / proxy 给的 ID 跟官方完全不一样

## 决策

`ADAPTER_MODELS["claudeCode"] = []`,`["opencoder"] = []`,`["codex"] = [...保留少量]`。

UI 检测到空列表 → 切 **`isForcedManual` 模式**:藏掉 dropdown,只留 text input + 说明文字(`ADAPTER_MODEL_HINT[adapter_id]`)指引用户查阅官方文档或跑 `opencode models`。

## 为什么

- **不撒谎** — 不会列出用户实际没法用的模型
- **稳健** — 模型 ID 改了不会影响 Polynoia,用户自己更新即可
- **诚实简单** — 比维护一份永远过期的列表好

## 否则会怎样

- 维护成本高:每出新模型就改 Polynoia 代码 + 兼容旧用户
- 用户体验有 trust issue:看见 "GPT-5.1" 选了发现连不上

## 代价

- 第一次用 Polynoia 的用户需要查文档拿模型 ID — 通过 hint 文字 + 文档链接缓解
- Codex 保留了 ["gpt-5.1", "gpt-5"] 因为 Codex 几乎只跑 OpenAI,差异性低

## 反例(暂时保留预设的情况)

Codex 列表保留是因为它的模型空间确实就是 OpenAI 的几个,差异性低,带个默认值用户体感更好。Claude Code / OpenCode 模型空间太开放,不适合。
