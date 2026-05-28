# Polynoia 上下文系统设计

> Cross-conv memory + per-agent privacy + compression。让每个 agent 像真人一样
> 感知自己参与过的所有事。
> 2026-05-27

## 1. 需求(用户原话)

> 怎么让 Agent 去感知,像人一样去感知他做过的那些事情,即使可能存在于不同的对话之中,
> 也包括那些代码修改,包括群聊里别人的修改的代码。又怎么设计上下文压缩机制呢?
> 如何考虑不同的 agent 可能有不同的上下文这件事?

拆成 4 个明确子问题:

1. **跨 conv 文本记忆**:agent A 在 conv X 说过 / 看过的话,在 conv Y 应该能引用
2. **代码变更感知**:其他 agent 在共享项目里改了代码,本 agent 进入项目时应该知道
3. **压缩策略**:历史越长,prompt 越爆。要在保留有用信息的前提下控制 token
4. **per-agent 个性化视野**:Claude-Fast 跟 Claude-Hardcore 是两个独立联系人,看到
   的世界应该不同(各自只看到自己参与过的 conv,不串扰)

## 2. 数据模型回顾

- `AgentRow` — 联系人(`adapter_id`+`model`+`system_prompt` 等),ULID id
- `ConversationRow.members: list[agent_id]` — 谁在这个 conv 里
- `MessageRow` — 所有消息(用户 + agent),按 conv_id 分组
- `WorkspaceRow.members: list[agent_id]` — 谁在这个项目里
- Sandbox per-conv `~/sandbox/polynoia/<conv_id>/`:每个 conv 一个 git repo,
  agent 通过 MCP tools 修改文件会触发 git commit

## 3. 五层 context 模型(per agent turn)

```
L1 Identity         (静态, per contact)
  · 你是 谁(name / handle / adapter / model)
  · 你的人格(setup.system_prompt)
  · 平台说明(Polynoia 多 agent IM,@-mention 规则)

L2 Project Briefs   (动态查 DB, 当前 agent 是成员的 workspaces)
  · workspace name / desc / repo / 成员
  · 当前 conv 所在 workspace → 详情;其他 workspace → 缩略一行

L3 Activity Ledger  (动态查 DB, agent 参与过的 conv 最近事件)
  · 最近 N 条跨 conv 事件,按时间倒序
  · 事件类型:文本消息 / 代码 commit / 工具调用 summary
  · 隐私:只包含 agent 是成员的 conv 的内容

L4 Conv History     (当前 conv 完整或滚动窗口)
  · 默认最近 30 条
  · 滚出 30 的旧消息:P0 截断,P1 用 cheap LLM 压成 summary block

L5 User Turn        (本轮用户消息)
  · 直接拼到末尾
```

## 4. 隐私模型(per-agent 个性化)

| 内容 | 谁能看到 |
|---|---|
| L1 自身身份 | 自己 |
| L2 项目档案 | 所有该项目成员(每个 agent 看到自己参与的项目) |
| L3 跨 conv 文本 | 只看到自己参与过的 conv 的消息 |
| L3 代码 commit | 同一 workspace 下所有 agent 都能看到 |
| L4 当前 conv 历史 | 本 conv 成员 |

具体规则:
1. **agent 不在的 conv 完全不可见**(包括别人之间的 1v1)
2. **共享 workspace 的代码变更对所有 workspace 成员可见**(不论变更发生在 workspace
   的哪个 conv 里)
3. **L3 cross-conv 文本只来自 agent 参与过的 conv**

注意 1A 决策:两个 contact 共用同一 adapter(e.g. Claude-Fast + Claude-Hardcore
都用 claudeCode)是**独立人格**,各自的 L3 ledger 互不串扰。

## 5. 压缩策略

总 token budget:60k(给主流 model 留足 reasoning + tools 余量)。

按优先级分配:
| 层 | 分配上限 | 超出后处理 |
|---|---|---|
| L1 Identity | 2k | 截断 system_prompt 末尾 |
| L2 Project briefs | 3k | 名字+描述,不展开内容;超 10 个项目只列前 10 个+总数 |
| L3 Activity ledger | 15k | 按时间倒序 fill,超额停止追加 |
| L4 Conv history | 35k | rolling window;超出最老的 P0 直接丢,P1 压缩成 summary |
| L5 User turn | ~5k | 用户输入,不动 |

**P0 实现:hard truncation**(简单可靠)。
**P1 实现:summarizer 子任务**(用 cheap model 把丢出的旧消息合成 200 字摘要,塞回 L4 头部)。

## 6. 模块布局

```
polynoia/context/
├── __init__.py       export build_context_for_turn
├── _types.py         ContextLayer / ContextBundle
├── identity.py       L1 — 静态拼装
├── briefs.py         L2 — workspace 查询
├── ledger.py         L3 — cross-conv 消息 + git
├── history.py        L4 — 当前 conv
├── window.py         token 估算 + 截断
└── assembler.py      总装 → 最终 prompt string
```

## 7. token 估算

P0 用粗略估算:`len(text) // 3`(中文 / 英文混合,经验值,1 个汉字 ~= 1-2 token,
1 个英文单词 ~= 1.3 token,1 字符 = ~1/3 token 近似)。
精确估算需要 tokenizer(`tiktoken` 之类),P1 接入。

## 8. 集成点

替换 `run_adapter_turn` 中的:
```python
history = sandbox.render_timeline_for_agent(agent_id)
prompt = f"{history}\n\n---\n\n{text}" if history else text
```

为:
```python
from polynoia.context import build_context_for_turn
async with SessionLocal() as db:
    prompt = await build_context_for_turn(
        db=db,
        agent_id=agent_id,
        conv_id=conv_id,
        user_text=text,
    )
```

返回值是完整 prompt string(已含五层),adapter.send() 直接吃。

## 9. 代码变更感知(L3 中的 git commit 子流)

每个 sandbox `~/sandbox/polynoia/<conv_id>/.git` 是该 conv 的 git。
通过 MCP 工具(write / edit / apply_patch)修改文件会自动 commit。

Ledger 拉 commit 时:
1. 找到当前 agent 是成员的所有 conv
2. 对每个 conv 跑 `git log --since=7d --format=...`
3. 解析 commit message — 我们的 MCP commit 模板包含 agent_id
4. 把 "agent A 在 conv X 修改了 file Y" 转成 ledger 条目

跨 conv 但同 workspace 的代码 commit:
- workspace 一旦关联 repo,该 workspace 下所有 conv 的 commit 视为 workspace 共享活动
- 即使 agent 没参与某个 conv,只要在 workspace 里,该 conv 的 commit 可见

## 10. 测试矩阵

1. **隔离测试**:agent A 在 conv1,agent B 在 conv2 — A 看不到 conv2 内容
2. **共享 workspace 代码可见**:A 和 B 同 workspace,A 的 conv1 改了文件 — B 进 conv2 能看到这个改动
3. **跨 contact 隔离**:Claude-Fast 和 Claude-Hardcore 都基于 claudeCode adapter,
   但各自只看到自己 ledger
4. **压缩触发**:塞 100 条消息进 conv,L4 应触发 rolling window 只保留最近 30
5. **L3 上限**:塞跨 5 个 conv 各 50 条消息,L3 应只填到 15k token 上限

下一步实施 §6 的 7 个文件 + tests/context/test_context.py。
