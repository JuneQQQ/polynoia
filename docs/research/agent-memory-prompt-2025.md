# 2025 前沿:多 Agent 的「记忆」与「prompt」——四篇正文深读 + 对 Polynoia 的重判

> 读的是正文(非摘要)。结论直接影响我们的 `<shared_memory>` 与 orchestrator prompt 设计。
> 配套决策:[ADR-019](../ADR/ADR-019-agent-level-memory-and-scenario-context.md)(本轮落地)、[ADR-014](../ADR/ADR-014-handoff-contract-and-shared-memory.md)(被修订)。
> 上两轮:`docs/diagrams/ruflo-learnings.html`(RuFlo 交接契约 + 共享记忆)。

## 一、四篇(机制 + 数字)

### ① MASS — Multi-Agent System Search(arXiv 2502.02533,Google)
把"一个 agent 的 prompt"形式化为三件套:**role 声明 + instructions + few-shot demos**。三阶段联合搜索(block prompt → topology → workflow prompt),用 MIPRO 贝叶斯优化。最硬实证(Gemini 1.5 Pro,Table 5):

| 配置 | 准确率 |
|---|---|
| 裸 agent | 63.54% |
| + block 级 prompt 优化 | 67.44%(单点 +4pt) |
| + topology 优化 | 77.55% |
| + workflow 级 prompt 优化 | **78.40%** |

- **prompt 优化是 token-效益单点最大杠杆**,胜过 self-consistency / debate 等拓扑花活("Only debate brings 3% gain while others fail to improve or even degrade")。
- 第三阶段必须把 prompt 重调成**编排感知**:"ensuring that prompts are tailored for orchestration within the MAS" —— agent 的 prompt 要随它在系统里的位置/职责重写,不能各写各的。

### ② Intrinsic Memory Agents(arXiv 2508.08997)
明确反对"所有 agent 共享同一份 homogeneous 记忆":"homogeneous memory for the agents, decreasing the benefits of having agents focused on a single part of the task"。做法:**每个 agent 一份 role-specific 的结构化(JSON)记忆**(槽位如 domain_expertise / current_position / proposed_solution),用 agent **自己的输出**更新(intrinsic),非外部统一摘要。注入顺序(Algorithm 1):任务描述 → 该 agent 的结构化记忆 → 最近几轮对话。效果:PDDL 上比 MetaGPT **+38.6%**,token 多 32% 但 token 效益最高。

### ③ G-Memory(arXiv 2506.07398,NeurIPS 2025)
多 agent 分层记忆,三层图:**insight graph**(跨任务可泛化洞见)/ query graph / **interaction graph**(压缩后的协作轨迹)。新 query 双向遍历,同时取回"高层洞见 + 压缩轨迹"。关键:区分 **team 级**(策略洞见)和 **agent 级**(各自协作轨迹);不改原框架即可挂载;成功率/准确率最高 **+20.89% / +10.12%**。

### ④ Context Engineering 综述(arXiv 2507.13334,过 1400 篇)
把记忆注入形式化为带约束优化:`arg max I(Y*; c_know | c_query) s.t. |C| ≤ L_max` —— 长度预算内选与答案**互信息最大**的记忆。两大动作:**selection**(选)+ **compression**(压)。多 agent 侧引入 `c_state`(用户/世界/MAS 的动态共享状态)作为显式注入分量;列了协议动物园(MCP / A2A / ACP / ANP)。

(辅:Blackboard 架构 arXiv 2507.01701 —— 把"所有 agent 同读同写一块黑板"作对比基线。)

## 二、范式迁移:老 school → 2025 前沿

| | 老 school(CrewAI/AutoGen/Swarm) | 2025 前沿(上四篇) |
|---|---|---|
| **per-agent prompt** | 人手写 role+goal+backstory,固定 | 自动优化,且随拓扑/职责重写(MASS) |
| **记忆 / 共享上下文** | 一份 blackboard/scratchpad,所有 agent 同读同写(homogeneous) | per-agent 异构结构化记忆(IMA)+ 检索式分层注入(G-Memory)+ 互信息最大化的选+压(综述) |

## 三、对 Polynoia 的重判 + 本轮落地

我们的 `<shared_memory>`(`context/shared.py`)此前是 **2023 级的扁平 blackboard**:conv-scoped、50 条全量、对每个 agent 一模一样注入、无预算上限、不分层。恰好踩中三个被前沿淘汰的点:

1. **homogeneous** —— 人人同一份 → IMA 说削弱"各管一摊"的专长优势。
2. **无检索/无压缩** —— 全量灌 50 条 → 综述的 naive baseline,应 `max I(Y*;·)` 的 selection + compression。
3. **不分层** —— 契约/决策/产物拍平 → G-Memory 说该分"可泛化洞见 vs 压缩轨迹",甚至 team 级 / agent 级。

而 orchestrator 角色不进 prompt(纪律塞在工具 description)正是 MASS 实证里的反模式(prompt 是单点最大杠杆且要编排感知)。

**结合我们的真实场景**(用户要在项目外单聊问询某 Agent 的工作:知自己工作 + 知队友相关工作 + 真读代码;项目外只读、项目内才写),本轮(ADR-019)做**廉价但对齐方向**的一步:

- **agent-level 记忆**(IMA「per-agent」+ G-Memory「agent 级 vs team 级」的最小落地):项目外单聊改为按 `author_agent_id` 注入"我的工作(跨对话)"+ workspace JOIN 注入"队友相关工作(摘要)"。复用既有列,零 schema 改动。
- **分层 + 预算 + 压缩**(G-Memory 分层 + 综述 `|C|≤L_max` 的廉价近似):shared_memory 按 kind 分层渲染(契约/决策在前必守,产物折叠成 headline);补上此前缺失的 `shared_memory` token 预算上限(从 history 的 0.62 切 0.08 给它)。
- **prompt 匹配场景**(MASS):项目外单聊 banner 改为反映"可只读项目代码 + 回顾工作",去掉与新能力矛盾的措辞。

**显式推迟**(写入 ADR-019「何时反悔」):
- 群聊里按 role 差异化注入 shared_memory(IMA 完整版)—— 快速跟进。
- 编排感知 prompt 的动态重写(MASS 完整版,is_orchestrator/group/DM 变体)—— 快速跟进。
- G-Memory 式 insight/trajectory 分图 + 向量检索 —— 与 ADR-014「故意不做向量」一致,远期。
- MIPRO/SONA 自动 prompt 优化 —— 远期。

## Sources
- MASS: Multi-Agent Design — Optimizing Agents with Better Prompts and Topologies — arXiv 2502.02533
- Intrinsic Memory Agents: Heterogeneous Multi-Agent LLM Systems through Structured Contextual Memory — arXiv 2508.08997
- G-Memory: Tracing Hierarchical Memory for Multi-Agent Systems — arXiv 2506.07398(NeurIPS 2025)
- A Survey of Context Engineering for Large Language Models — arXiv 2507.13334
- Exploring Advanced LLM Multi-Agent Systems Based on Blackboard Architecture — arXiv 2507.01701
