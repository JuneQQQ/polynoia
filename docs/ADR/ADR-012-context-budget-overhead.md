# ADR-012 — Context budget = max_context − Claude Code 固定开销

- **状态**:accepted
- **日期**:2026-05-29
- **相关**:`apps/server/polynoia/context/budget.py` + ADR-002(5 层 context)

## 背景

ADR-002 把上下文拆 5 层(identity / briefs / ledger / history / user_turn),每层有 token 预算。**之前所有层加一起 60k**(2k + 3k + 15k + 35k + 5k)— 这是一开始假设所有 agent 跑 Anthropic 直连、其余预算留 Claude Code 自管。

但用户的实际配置远比这复杂:
- Claude Code 可以接 LiteLLM / 小蜜蜜 / 月之暗面 等第三方代理
- 这些代理后端可能是 Kimi(200k)/ MiMo(262k)/ DeepSeek(128k)/ GPT-5(256k),token 边界差很大
- Claude Code 自带的上下文估计**只对 Anthropic 模型准**,接第三方就会偏离

用户实测发现 60k 远远没用完模型上限。要求:
1. 让用户在创建联系人时**手填模型最大长度**
2. 自动按 model id 给个合理默认
3. 留够 Claude Code 自己的**固定开销**(系统 prompt / MCP tools / tool churn / 输出 reserve)

## 决策

**`compute_budget(model, max_context_override?)`**(`context/budget.py`)按下面公式动态算 LayerBudget:

```
max_total       = override ?? KNOWN_MODEL_CONTEXT[model] ?? 128_000(fallback)
available       = max(30_000, max_total − 35_000)        # 35k 固定开销
LayerBudget = {
  identity   : max(2k,  available × 0.04),
  briefs     : max(3k,  available × 0.06),
  activity   : max(15k, available × 0.18),
  history    : max(35k, available × 0.62),  # 占大头
  user_turn  : max(5k,  available × 0.10),
}
```

### `CLAUDE_CODE_OVERHEAD = 35_000` 的拆分

| 项 | 估值 | 依据 |
|---|---|---|
| Claude Code built-in system prompt | ~5k | preset="claude_code" 模式;含 tool-use 约定 / file ops / security / todo 行为 |
| MCP tool definitions(9 Polynoia + 2 built-in)| ~3.5k | 每 tool description + JSON schema ~300 tokens |
| In-turn tool churn buffer | ~18k | 多文件 edit + read 累计可能 15-25k tool I/O |
| Output reserve(Claude 生成 reply 的空间)| ~8k | 典型 reply 1-3k 文本,留出余量 |
| **合计** | **~35k** | |

### Known-models 表

`KNOWN_MODEL_CONTEXT` 列了 ~17 个常见模型,key 是 lowercase model id。匹配顺序:
1. 完整 id(`claude-opus-4-7`)
2. 去掉 provider 前缀(`anthropic/claude-opus-4-7` → tail `claude-opus-4-7`)
3. 子串模糊匹配

未命中 → fallback 128k(覆盖 GPT-4o / DeepSeek V3 / Llama 等常见 128k 模型)。

### Claude `[1m]` 变体

Anthropic 的 Opus 4.7 / Sonnet 4.6 提供 **1M-token beta** 版本,模型 id 加 `[1m]` 后缀(例 `claude-opus-4-7[1m]`)。表里**优先匹配带后缀的 key**,所以 `[1m]` 不会被 substring 模糊匹配吞回 200k。lookup 顺序:**exact → 带 provider 前缀的 tail exact → substring**,前者命中就停。

## 为什么

- **大头给 history(62%)** — 多轮 agent 协作里,L4 当前对话历史最常被回查,它撑爆比 ledger 撑爆代价大得多
- **固定 35k 而非 30%** — 实测 35k 不随上下文规模变,因为 system prompt + tool defs 是固定大小,tool churn 也跟"任务复杂度"挂钩而非"模型大小"。30% 在 200k 模型下会留 60k 给 overhead,但 Claude Code 实际只吃 35k,白白浪费 25k 历史窗口
- **floor 保护**(`max(35k, ...)`)— 小模型(50k 之类)算出来的 history 才 9k,直接撞 floor 35k 拦,起码保住和之前一样
- **available 至少 30k clamp** — 用户填了荒唐的 10k,不能让算出来变负数

## 否则会怎样

- 写死 60k:大模型(MiMo 262k / GPT-5 256k)历史窗口浪费 70-80%,agent 看不到太多上文
- 按 30% 算 overhead:浪费 + Claude Code 真实开销变了我们感知不到
- 不让用户改:第三方代理用户(很多)无法 tune,默认值要么过保守要么过激

## 代价

- 用户需要懂"上下文长度"概念,UI 加了一个可选 field
- KNOWN_MODEL_CONTEXT 表是手维护的,**每 6-12 个月需要扫一遍**(Anthropic 出新版 / 新 backend 上线)— 落进 docstring 提醒
- 我们的 token 估算只是粗略(CJK ×1.5,latin /3.5);精确估算需要每 model 跑实际 tokenizer。暂不做

## 何时反悔

- Claude Code SDK 暴露真实"剩余可用 token"接口:直接用,不用拍 35k
- 各 backend 都开放精确 tokenizer 接口:换掉 `estimate_tokens` 的粗算
- 用户大批量出 bug 反馈"上下文不够":把 history 比例从 62% 调到 70%
