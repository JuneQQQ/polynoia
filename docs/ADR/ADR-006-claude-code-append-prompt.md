# ADR-006 — Claude Code system_prompt 用 append 模式

- **状态**:accepted
- **日期**:2026-05-26
- **相关**:`apps/server/polynoia/adapters/claude_code.py`(_translate_claude_stream 上方 SystemPromptPreset 构造)

## 背景

`claude_agent_sdk` 提供 `SystemPromptPreset` 让 client 自定义 system_prompt。两种模式:
- **Override**:完全替换内置 system_prompt
- **Append**:在内置 system_prompt 后面追加自定义文字

我们要把"你是某某联系人,角色是 X"这种 persona 信息给到 Claude Code。

## 决策

**用 append 模式**:
```python
SystemPromptPreset(
    preset="claude_code",      # 保留内置默认
    append=user_persona,        # 我们的 persona 附加在后
)
```

绝对不 override `preset="custom"` 完全替换。

## 为什么

- **保留内置 prompt** — Claude Code 内置的 system prompt 给了 Claude 关于工具调用约定、文件操作规范、安全边界等的大量约束。覆盖了 → Claude 变成"裸 Claude",行为退化,工具调用都不对了
- **降低维护成本** — 我们不用追踪 Anthropic 每次升级 Claude Code system_prompt 改了啥
- **persona 信息只是补丁** — 我们要加的只是"你叫小美,角色是后端 specialist",这种小补丁 append 在末尾就够了

## 否则会怎样

- 完全覆盖 → Claude 在工具调用、安全检查、文件路径处理上行为大幅退化
- 实际试过 → 直接 hidd 出"找不到 Read 工具"之类的错误

## 历史

P0 早期一度写成 override 模式(因为没注意 SDK 有 preset),结果 Claude Code 行为乱七八糟,debug 一上午才定位。

## 类似场景

OpenCode adapter 不需要这层 — `opencode run` CLI 把 system prompt 当 user message 第一段 prepend,我们直接走它默认行为即可。Codex 同样。
