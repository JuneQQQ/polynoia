# 图示:Agent Context 构成(把 Agent 当真实同事)

**主题**:每次 Agent turn 的 LLM prompt 由 **5 层 memory 组合**而成,代表它的"职场记忆"。
群聊有完整记录(含 tool call 全程),但 LLM 看到的是经检索 + 摘要后的精炼上下文,而非整段历史。

**用途**:答辩讲"为什么 agent 像同事而不像 chatbot",给团队 onboard 时讲数据模型的真正用法。

## 我理解的需求(verify 用)

| # | 你的要求 | 我的理解 |
|---|---|---|
| 1 | 每个群聊有完整记录(thought / tool call / result) | 全量存 `messages` 表 + `audit.jsonl` + `timeline.jsonl`,长但留 |
| 2 | Agent 在群里只看最近 10 条相关 | conv-local window:`last_N=10` 的 messages,经 relevance 过滤 |
| 3 | 单聊跟 Agent 时,它知道之前在群里 user 让它做过什么 + Orchestrator 分配 + 它完成情况 + 别人做了啥 | cross-conv episodic memory:**按 (agent_id, user_id) 跨 conv 检索**它对该 user 的历史协作 |
| 4 | 当真实同事看待 | 永久身份 + 历史经验 + 当前 working memory 三层叠加 |
| 5 | 加 RAG | semantic retrieval 作为第 5 层,补全前 4 层覆盖不到的远期事件 |
| 6 | 不直传完整群聊给 LLM | 经 context builder 选最相关 → 摘要 → XML 注入 prompt;总 token 严格控制 |

## GPT-IMAGE-2 Prompt(高信息密度,16:9)

```
生成一张高信息密度的中文技术信息图海报,主题是:

《Polynoia Agent Context Composition · 把 Agent 当真实同事看待的 5 层记忆模型》

画布与风格:
16:9 横版构图,极浅灰背景 #FBFAF7,清晰矢量风格,论文技术海报风格,模块化网格、
紧凑标签、大数字指标。不要赛博朋克,不要机器人,不要 AI 大脑,不要人物插画。

语言要求:
图中主要文字使用简体中文。技术标识保留英文:
identity / working memory / conv-local recent / cross-conv episodic / RAG /
semantic retrieval / token budget / system_prompt / tool calls / tool results /
timeline.jsonl / audit.jsonl / messages table / vector index / sqlite-vec /
top-K / context_builder / agent_id / user_id / conv_id / @mention chain /
turn_id / orchestrator_profile / cosine similarity / 30d recency window
不要生成乱码、伪中文、错别字。不要添加未在 prompt 中出现的新字段或新表名。

整体布局:六个清晰分区,左右纵向布局 + 中部主流程图 + 底部生成的 prompt 示例。

顶部标题栏:
大标题:Agent = 真实同事 · 5 层记忆模型
副标题:身份永固 + Turn 内即时 + 本群最近 10 + 跨群同事关系 + RAG 远期检索 →
       XML 结构注入 LLM · 不直传完整群聊
右侧 4 个徽章:
"5 层记忆"
"per-turn 重组"
"token budget 8K"
"不直传全聊"

中部主图(占大半画面):横向"原始数据 → context_builder → LLM prompt"管线。

═══ 左侧:5 层 Memory(每层一个矩形,从上到下排) ═══

L1 — Identity Layer (永固,棕色)
内容:
- system_prompt (Agent.system_prompt)
- role (Agent.role / orchestrator_profile)
- caps[] / tools_whitelist[]
- provider 元信息 (color, handle, initials)
存储:agents 表 (SQL)
注入策略:每个 turn 都传,不变

L2 — Working Memory (本 turn 内,蓝色)
内容:
- 当前 user prompt (本 turn 输入)
- 已发生的 tool calls + results (本 turn 累积)
- chain @-mention 的祖先 agents 发言 (depth>0 时)
存储:in-memory (turn 结束即丢)
注入策略:逐工具调用累加

L3 — Conv-Local Recent (本群聊,绿色)
内容:
- 本 conv 最近 10 条 messages (按 timestamp DESC)
- 经 relevance 过滤:与当前 prompt 关键词或语义相关
- pinned context (Pin 表内的 doc/color/user/ref)
- 自己在本 conv 的全部发言历史 (timeline.jsonl 自己的 entries)
存储:timeline.jsonl (per conv) + messages 表 + pins 表
注入策略:截断后摘要,token 上限 1500

L4 — Cross-Conv Episodic (跨群同事记忆,紫色)★ 关键创新
内容(只取与当前 user_id 相关的):
- 此 user 在其它 conv 让我做过什么 (按 (sender=user, mentions⊇self) 查)
- Orchestrator 给我分派过的任务 + 我的完成情况
  → 从 audit.jsonl agent.dispatch 找 callee=self;
    然后查我相关的 commits (sandbox git log --author=self)
- 同伴 agent 在相关 conv 的关键产出 (只取关键 anchor)
  → 例如:claudeCode 设计完 schema 后,codex 实现的关键 commit
- "她说的就是这个 user 上周让我做的事" 的同事识别
存储:跨 sandbox 的 timeline.jsonl 聚合查询 + messages 表跨 conv 索引
注入策略:摘要 + 链接 ID,token 上限 1000

L5 — RAG Semantic Retrieval (远期模糊检索,橙色)
内容:
- 所有 conv 的 message + tool_result 向量化
- query = current user prompt embedding
- filters:
    (agent_id=self) AND
    (participants_include=[self, user]) AND
    (time_window <= 30d)
- 取 top-K=5 chunks
- 用于 L3/L4 覆盖不到的远期、隐含相关的事件
存储:vector index (sqlite-vec 嵌入 polynoia.db / 或 chromadb)
注入策略:top-K chunks + cosine score, token 上限 800

═══ 中间:context_builder 管线 (大箭头,小米橙) ═══

input  →  context_builder(agent_id, user_id, conv_id, current_prompt)
            ├── 查 L1 → fetch agent identity
            ├── 累 L2 → take this-turn tool calls
            ├── 查 L3 → fetch last 10 + relevance filter + pins
            ├── 查 L4 → cross-conv episodic (key for "remember me"!)
            ├── 查 L5 → semantic retrieval top-K
            └── compose XML prompt within token budget 8K

═══ 右侧:LLM 拿到的最终 prompt 结构示意 (XML) ═══

显示一个紧凑代码块,标题"Composed Prompt → LLM":

<agent_identity>
  你是 @claudeCode (Code Designer 角色)
  能力:design / docs / refactor
  工具白名单:read, edit, write, ...
  Style 指南: ...
</agent_identity>

<current_conv kind="dm" with="@you">
  <recent_messages limit="10">
    <msg id="01..." sender="you" t="14:32">
      帮我看一下 calculator.py 的 tax 计算
    </msg>
    <msg id="01..." sender="claudeCode" t="14:33">
      [自己之前的回复摘要 80 字]
    </msg>
    ...
  </recent_messages>
  <pinned_context>
    <pin kind="doc" label="PRD v0.3" ref="#prd-031" />
  </pinned_context>
</current_conv>

<cross_conv_memory>
  <prior_collab_with user="you">
    上周三在 #conv-webhook-router 你让我设计 schema,
    我交付了 5 个 model + Alembic migration (commit 5926333).
    Orchestrator 让 @codex 实现 Go 版本,
    @opencoder 写测试 (16 tests pass).
  </prior_collab_with>
  <orchestrator_tasks_assigned_to_me>
    [汇总 audit.jsonl agent.dispatch callee=claudeCode 的最近 5 条任务]
  </orchestrator_tasks_assigned_to_me>
  <peer_recent_work>
    @codex (3 天前): 修复 calculator.py 的 tax bug (commit abc123)
    @opencoder (昨天): 加了 invoice tests
  </peer_recent_work>
</cross_conv_memory>

<retrieved_relevant top_k="5">
  <chunk src="conv-billing-q3" score="0.87">
    "tax_rate 取自 config.toml,不要硬编码" — 你说过 (4 周前)
  </chunk>
  ...
</retrieved_relevant>

<user_message>
  上面 user 当前 prompt 原文
</user_message>

底部分区 6 — Token 预算与存储映射 (横向表格,占满底部宽度):

层级 | 内容来源              | Token 预算 | 存储后端                          | 检索策略
─────┼──────────────────────┼───────────┼─────────────────────────────────┼─────────────────
L1   | agents 表             | ~500       | SQL (P0 已落) 仅 system_prompt     | 直接读
L2   | turn-scoped 累积      | 动态 1-3K | in-memory (asyncio 局部变量)       | 累积,无需检索
L3   | conv 最近 10          | ~1500     | timeline.jsonl + messages 表       | 时序 DESC LIMIT 10 + 关键词过滤
L4   | 跨 conv 同事关系       | ~1000     | messages 表 + timeline jsonl 聚合 | (agent, user) 索引查询 + 摘要器
L5   | RAG top-K=5           | ~800      | sqlite-vec / chromadb (P1+ 待加) | cosine + filters
─────┴──────────────────────┴───────────┴─────────────────────────────────┴─────────────────
总预算 ≤ 8K tokens · 超出时按优先级丢弃:L5 → L4 → L3 → L2 → L1 (L1/L2 永远保留)

设计要求:
5 层 memory 用不同色系: L1 棕 #8B6F47 / L2 蓝 #5B8FF9 / L3 绿 #27AE60 /
L4 紫 #9B59B6 (强调,关键创新) / L5 橙 #F2994A
小米橙 #F2994A 作为 context_builder 管线箭头颜色 (中部主流程)
表格细线 #E5E7EB,黑色文字 #1F2937
每个 memory 层框内,用大字号显示 token 预算
XML 代码块用等宽字体,行高紧凑
信息密度高但层级分明,色彩区分清晰可读。
不要添加任何 prompt 中没有的字段、表名或数字。

Aspect ratio: 16:9.
```

## 5 层 Memory 解释(每层 1 段)

### L1 — Identity Layer(身份永固)
来自 `agents` 表的 `system_prompt` / `role` / `caps` / `tools_whitelist`。**只要这个 agent 存在,就永远是它**。换 conv 不变、换 turn 不变。

### L2 — Working Memory(本 turn 内即时)
当前 user prompt + 已发生的 tool call + tool result + chain @-mention 的祖先发言。**turn 结束即丢**。

### L3 — Conv-Local Recent(本群最近)
**最近 10 条 messages** 经 relevance 过滤 + 自己在本 conv 的全部发言 + Pin。**这是你说的"群里最近 10 条"层**。

### L4 — Cross-Conv Episodic Memory(★ 跨群同事记忆,关键创新)
**这就是你描述的"单聊时记得在群聊里 user 让它做过什么"**。查询规则:
```
WHERE (sender_id = user_id AND mentions ⊇ self)         ← user 之前让我做的
   OR (audit.event_type = "agent.dispatch" AND callee = self
       AND parent_user_id = user_id)                     ← Orchestrator 分给我的
   OR (sender_id ∈ peer_agents AND in_same_workspace)    ← 同伴做了啥
```
不直传 message 原文,而是 **摘要器**(可以是 LLM 或简单规则)生成 1-3 句 "上周三 user 让我设计 schema,我交付了 5 个 model"。

### L5 — RAG Semantic Retrieval(远期模糊召回)
所有 message 向量化后建索引。Query-time 用当前 prompt embedding 检索 top-K=5。Filters 强制:`agent_id=self AND participants⊇{self, user} AND time_window ≤ 30d`。**这层用来补 L3/L4 漏的"很久以前我说过、相关概念的事"**。

## 实施工程量预估(画完图后才动代码)

| 改动 | 影响范围 | 工时估 |
|---|---|---|
| Messages 表已存在(SQL 完成) | 0 | 0 |
| `polynoia/context/builder.py` 新模块 | 新建 ~300 行 | 1-2 天 |
| `polynoia/context/episodic.py` cross-conv 查询 + 摘要器 | 新建 ~200 行 | 1 天 |
| `polynoia/context/retrieval.py` 接 sqlite-vec | 新建 ~150 行 | 1 天 |
| 每个 Adapter 的 prompt 前注入 context_builder 结果 | 改 3 个 adapter | 0.5 天 |
| Token 预算控制器(超出时按 L5→L1 优先级丢弃) | 新加 ~80 行 | 0.5 天 |

总共 4-5 天。但**先确认你认同这个 5 层模型**再开工。

## 关键问题想 verify

1. **L4 "我做了什么 + Orchestrator 分配 + 别人做了啥" — 这 3 项摘要分开还是合一**?
   - 我倾向**分 3 段子标签**(`<my_work>` / `<orchestrator_tasks>` / `<peer_work>`),agent 容易引用
2. **"相关"怎么定义**?L3 取最近 10 条 → 全取 / 经语义过滤 / 经关键词过滤?
   - 推荐:**默认全取最近 10**,token 紧时再用语义打分丢弃
3. **L5 RAG 的 vector backend**?选 `sqlite-vec`(嵌入现有 polynoia.db,零部署)还是 `chromadb`(独立服务,功能更强)?
   - 推荐 P1 用 sqlite-vec(简单),P2 看体量决定迁移 chromadb
4. **"完整群聊记录"存哪里**?
   - SQL `messages` 表(已建)+ timeline.jsonl(已有)+ audit.jsonl(已有)= 三处冗余
   - 答辩说:**事实层 messages(全量) → 时间层 timeline(打标了 mentions/depth) → 审计层 audit(tool 粒度) → 给 LLM 用的是 builder 的产物**

## 关联

- `apps/server/polynoia/domain/entities.py` — Agent / Conversation / Pin 字段定义
- `apps/server/polynoia/storage/models.py` — SQL 表
- `apps/server/polynoia/sandbox/_core.py::append_timeline / render_timeline_for_agent` — L3 的雏形
- 前置图:`polynoia-data-schema.md` 讲存储分层
- 前置图:`agent-adapter-mechanics.md` 5 张图讲机制
