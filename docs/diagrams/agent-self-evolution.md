# 图示:Agent 自进化 — 2025-2026 调研与 Polynoia 路线图

**主题**:基于 2025 年下半年到 2026 年的最新开源项目 + 论文,设计 Polynoia 的 Agent 自进化层。

**用途**:答辩"我们不是用 2023 的 Reflexion / Voyager 老思路,跟上了最新前沿"。

> ⚠️ **本图替换上一版**:旧版用 2023-2024 的 Voyager / Reflexion / DSPy(MIPRO),已过期。

## 关键 2025-2026 工作(verify 过的)

| 工作 | 时间 | 来源 | 核心机制 |
|---|---|---|---|
| **Anthropic Skills**(SKILL.md) | 2025-10 | [claude.com/blog/skills](https://claude.com/blog/skills) | 把"专家知识"打包成 SKILL.md + 资源文件夹,Claude 在 conv 内自动按需 load。**不是自动学习,是知识装配**。组织级管理 2025-12 推出。 |
| **GEPA**(Reflective Prompt Evolution) | 2025-07, ICLR 2026 oral | arxiv 2507.19457 | 用**自然语言反思**做 prompt 进化,而非 RL gradient。对比 MIPROv2 高 10%,对比 GRPO 高 6% / 用 35× 更少 rollouts。"语言比 scalar reward 是更丰富的学习介质"。 |
| **GRPO**(DeepSeek R1) | 2025-01 | DeepSeek R1 paper | RL 的策略优化变体,用 group-relative reward,**已成为 reasoning agent 训练标准**(替代 PPO)。 |
| **Mem0**(memory layer) | 2025-2026 active | [github.com/mem0ai/mem0](https://github.com/mem0ai/mem0) | 多层 memory(user/session/agent state),不是 RAG(动态学,不是静态检索)。2026-04 更新:single-pass extraction、entity linking、temporal reasoning。 |
| **MemSkill** | 2026 | arxiv 2602.02474 | 闭环:RL 选 skill + LLM 从 hard case 进化 skill bank,**持续优化 skill 库**。 |
| **SAGE**(Skill-As-General-Experience) | 2025 | 论文 | 把过往交互转成可复用 skill,Scenario Goal Completion +8.9%,交互步数 -26%。 |
| **Audited Skill-Graph Self-Improvement** | 2025-12 | arxiv 2512.23760 | 把"自我提升"形式化为**编译 agent 进 skill graph**,带可验证 reward(tool 正确性、outcome、reuse、composition)+ experience synthesis。 |
| **Agent Memory Survey**(综述) | 2025 | [github.com/Shichun-Liu/Agent-Memory-Paper-List](https://github.com/Shichun-Liu/Agent-Memory-Paper-List) | 把 memory 分 token-level / parametric / latent 三种 form。 |
| **Adaptation of Agentic AI**(综述) | 2025-12 | arxiv 2512.16301 | post-training + memory + skills 三轴的四范式框架。 |

## 跟 2023-2024 工作的对比(为什么重写)

| 维度 | 2023-2024(旧版) | 2025-2026(新版) |
|---|---|---|
| Skill 抽象 | Voyager:**代码片段**作 skill(Minecraft) | Anthropic Skills + MemSkill:**markdown + 资源**作 skill,可被 LLM 自然 load |
| Prompt 优化 | DSPy MIPROv2:用 example pairs + bootstrap | **GEPA**:自然语言反思 + 进化(性能 +10-20%,代价 35× 少) |
| RL 算法 | PPO / DPO | **GRPO**(DeepSeek R1),group-relative reward |
| Memory 抽象 | Reflexion:verbal critique 写 memory bank | **Mem0 / MemSkill**:多层 memory + 持续 entity linking + 时间推理 |
| Self-improvement 范式 | "试错 + 反思"循环 | **Skill Graph + Experience Synthesis**(2025-12):形式化为可审计的编译过程 |

## Polynoia 重新规划的 4 机制(2025-2026 版)

### 机制 ① — Anthropic-Style Skills(★ 替代旧 Voyager)
- **思路**:用 Anthropic Skills 的 SKILL.md 格式(同行业标准,已 verified)
- **生成**:任务成功后(tests pass / git commit clean / user 👍),orchestrator LLM 抽象成 SKILL.md
- **存储**:`<sandbox>/_global/skills/<agent>/<slug>/SKILL.md` + 资源
- **激活**:每次 turn 开始,context_builder 扫 skills 目录,把相关 skill 装入 L1
- **优势**:跟 Claude Code 生态对齐 — 用户写的 skill 跟我们生成的 skill **格式一致,可互通**

### 机制 ② — GEPA Prompt Evolution(★ 替代旧 DSPy)
- **思路**:不用 gradient / RL,纯自然语言反思 + 进化
- **流程**:
  1. 收集最近 N 个 turn 的 (prompt, output, satisfaction)
  2. 多个 prompt 变体并行采 trajectory
  3. LLM 用自然语言诊断每条 trajectory 的"哪里好哪里差"
  4. 合并多个变体的"好 lesson"成新 prompt(genetic crossover with critique)
  5. Pareto 选最优(多目标:正确率 + 短 + 安全)
- **代价**:35× 少 rollouts,对小团队友好
- **存储**:每个 agent 的 system_prompt 版本化在 `agents` 表

### 机制 ③ — Mem0-Style Multi-Layer Memory(★ 替代旧 Generative Agents)
- **思路**:user-level / session-level / agent-state 三层 memory(Mem0 框架)
- **跟我们 L1-L5 context 模型的关系**:Mem0 提供 L3(conv-local)+ L4(cross-conv)+ L5(RAG)的统一存储 + 检索后端
- **关键 2026 更新**:single-pass extraction(1 次 LLM 调用提取所有事实)+ entity linking + temporal reasoning
- **集成**:直接装 mem0 Python SDK,跑在 Polynoia server 进程内
- **优势**:别造轮子,跟成熟开源 ecosystem 对齐

### 机制 ④ — Skill Graph Self-Improvement(★ 新加,2025-12 ASG-SI)
- **思路**:把每个 agent 的能力组织成 skill graph(DAG of skills),每完成任务就编译一次:
  - 验证 reward(测试通过 / 输出有效 / skill reuse)
  - Experience synthesis(自动 stress test 覆盖)
  - 连续 memory 控制(decay + reinforcement)
- **跟 ① 的关系**:① 产出叶子 skill,**④ 把 skill 组织成有依赖关系的图**
- **可视化**:Polynoia 加 view "Skill Graph" 展示每个 agent 的能力图
- **延迟**:P2(P1 先把 ①②③ 做出来再上)

## GPT-IMAGE-2 Prompt(高信息密度,16:9)

```
生成一张高信息密度的中文技术信息图海报,主题是:

《Polynoia Agent 自进化 · 2025-2026 前沿映射》

画布与风格:
16:9 横版构图,极浅灰背景 #FBFAF7,清晰矢量风格,论文技术海报风格,模块化网格、
紧凑标签、大数字指标。不要赛博朋克、不要机器人、不要 AI 大脑、不要人物插画。

语言要求:
图中主要文字使用简体中文。技术标识、论文/项目名保留英文:
Anthropic Skills / SKILL.md / GEPA / GRPO / DeepSeek R1 / MIPROv2 /
Mem0 / MemSkill / SAGE / ASG-SI / Voyager / Reflexion / DSPy /
skill graph / experience synthesis / entity linking / temporal reasoning /
Pareto / verifiable reward / continual memory / context builder L1-L5 /
audit.jsonl / timeline.jsonl / sandbox git
不要乱码、伪中文、错别字。所有论文名/年份/百分比严格按下面给出,不要新加。

整体布局:
顶部标题栏 + 中部 2025-2026 工作横向时间轴 + 4 个机制大卡 + 底部新旧对比表 + 风险与路线。

顶部标题栏:
大标题:Agent Self-Evolution · 2025-2026 前沿映射
副标题:替代 2023-2024 老思路 · Anthropic Skills + GEPA + Mem0 + Skill Graph
       四套机制 · 与 Claude Code / Mem0 生态对齐 · 不造轮子
右侧 5 个徽章:
"GEPA +10-20% over MIPROv2"
"GEPA 35× 更少 rollouts"
"Anthropic Skills 2025-10"
"Mem0 2026-04 single-pass"
"ASG-SI 2025-12 可审计"

第一区:2025-2026 工作时间轴 (横向,占顶部 1/4 高度)
时间轴从左到右:
2025-01  DeepSeek R1 / GRPO         "RL self-improve 标准算法"
2025-07  GEPA (ICLR 2026 oral)      "natural-language prompt evolution"
2025-10  Anthropic Skills           "SKILL.md 标准化"
2025-12  Anthropic Skills 组织级管理
2025-12  ASG-SI                     "skill graph + experience synthesis"
2026-04  Mem0 update                "single-pass extraction · entity linking · temporal reasoning"

第二区:4 个机制大卡 (并列,占中部主体,每卡 1/4 宽)

═══ 卡 ① Anthropic-Style Skills (绿色 #27AE60) ═══
对应前沿:Anthropic Skills (2025-10) + MemSkill (2026)
触发:任务 success (tests pass + git commit clean + user 👍)
处理:
  orchestrator LLM 抽象任务 → SKILL.md
  字段:name · description · when_to_use · resources/ · scripts/
存储:<sandbox>/_global/skills/<agent>/<slug>/SKILL.md
激活:每 turn context_builder 扫 skills,装入 L1 identity 层
优势:与 Claude Code / Anthropic 生态格式一致,可互通
对比旧版:Voyager 用 code skill (Minecraft 特化),新版 SKILL.md 通用
阶段:P1

═══ 卡 ② GEPA Prompt Evolution (橙色 #F2994A,小米橙强调) ★ ═══
对应前沿:GEPA (arxiv 2507.19457, ICLR 2026 oral)
触发:每 50 turn 做一次反思 + 进化
处理:
  1. 收集 (prompt · output · satisfaction)
  2. 多 prompt 变体并行采 trajectory
  3. LLM 自然语言诊断 (不用 RL gradient)
  4. genetic crossover 合并 lessons
  5. Pareto 选最优 (正确率 + 短 + 安全)
代价:35× 少 rollouts (vs GRPO)
收益:+10-20% (vs MIPROv2),+12% AIME-2025
存储:agents 表 system_prompt 版本化
对比旧版:DSPy MIPROv2 需 example pairs + 大量 bootstrap
阶段:P1

═══ 卡 ③ Mem0 Multi-Layer Memory (蓝色 #5B8FF9) ═══
对应前沿:Mem0 (2025-2026, github mem0ai/mem0)
三层 memory:
  user-level    持久用户偏好 / 历史
  session-level conv 上下文
  agent-state   系统行为 / 配置
2026-04 算法升级:
  single-pass extraction (1 LLM call)
  entity linking 跨 memories
  multi-signal retrieval (semantic+keyword+entity)
  temporal reasoning (时间感知)
集成:装 mem0 Python SDK 进 polynoia server 进程
跟 L1-L5 关系:Mem0 替我们做 L3+L4+L5 的存储与检索
对比旧版:Reflexion verbal memory bank · Generative Agents importance-decay
阶段:P1

═══ 卡 ④ Skill Graph Self-Improvement (紫色 #9B59B6) ═══
对应前沿:ASG-SI (arxiv 2512.23760, 2025-12)
思路:每个 agent 的能力组织为 skill graph (DAG)
每完成任务编译一次:
  verifiable reward (tool 正确性 / outcome / skill reuse / composition)
  experience synthesis (自动 stress test 覆盖)
  continual memory 控制 (decay + reinforcement)
跟 ① 的关系:① 产出叶子 skill · ④ 把 skills 组织成依赖图
可视化:Polynoia 加 view "Skill Graph"
对比旧版:无对应 2023-2024 工作,这是 2025-12 全新提出
阶段:P2 (P1 先做 ①②③)

第三区:新旧版本对比表 (横向 5 列,占下部)

维度       | 2023-2024 旧版      | 2025-2026 新版                  | 差距
───────────┼─────────────────────┼─────────────────────────────────┼───────
Skill 抽象 | Voyager 代码片段     | Anthropic SKILL.md + MemSkill   | 通用化
Prompt 优化| DSPy MIPROv2        | GEPA 自然语言进化               | +10-20%
RL 算法    | PPO / DPO          | GRPO (DeepSeek R1)              | reasoning agent 标准
Memory     | Reflexion verbal   | Mem0 multi-layer + 2026 算法    | 工业可用
形式化     | 无                 | Skill Graph (ASG-SI)            | 可审计

第四区底部:Polynoia 落地路线 + 风险表 (双栏)

左栏:4 阶段路线 (横向 timeline)
P0 (现在):
  - audit/timeline/git 已就绪 (evolution 数据源)
  - L3 (本群最近 10) 已实现 (timeline injection)
  - SQL persistence 已实现 (agents.system_prompt 可版本化)

P1 (4-6 周):
  + 机制 ① Anthropic-Style Skills (SKILL.md)
  + 机制 ② GEPA Prompt Evolution (按 50 turn 触发)
  + 机制 ③ Mem0 multi-layer memory (装 SDK,接 L4+L5)

P2 (8-12 周):
  + 机制 ④ Skill Graph Self-Improvement (ASG-SI)
  + Skill Graph 可视化 view
  + 自进化 explainability

P3:
  + 跨 agent skill transfer (opt-in)
  + Skill marketplace (跟 Claude Code Skills 互通)
  + RL fine-tune (GRPO,需大算力,暂不优先)

右栏:风险表 (红色 #E74C3C 边框)
| 风险                          | 缓解                              |
|------------------------------|----------------------------------|
| Prompt 漂移坏方向             | GEPA Pareto 多目标 + 人审 + revert  |
| Skill 累积垃圾               | usage_count<3 自动归档 (Anthropic 默认) |
| 集体 skill 跨 agent 传染坏行为 | 默认 per-agent 隔离 + opt-in        |
| Mem0 entity linking 错认     | confidence threshold + 人编辑入口   |
| Skill Graph 复杂度爆炸        | ASG-SI 内置 depth + width 限制       |
| Cost 失控                    | GEPA 已 35× 省,Mem0 single-pass    |

底部信息条 (灰色细字):
所有 evolution 数据源 P0 已就绪 · 不需新埋点
P1 装 mem0 + 加 SKILL.md 生成器 (~600 行 Python)
P1 加 GEPA 采样调度器 (~400 行)
P2 加 skill_graph 表 + 编译器 (~800 行)
跟 Claude Code Skills 格式互通 · skill marketplace 自然可接入

设计要求:
4 机制卡颜色:① 绿 #27AE60 / ② 橙 #F2994A 强调 / ③ 蓝 #5B8FF9 / ④ 紫 #9B59B6
时间轴 6 个里程碑用渐变,从 2025-01 浅蓝 到 2026-04 深橙
新旧对比表第三列(差距)用绿色 #27AE60 突出"提升"
风险表红边框
论文名 / GitHub repo 名用等宽字体,版本号 / 百分比用大字号
中部 4 卡纵向均高,卡内 5-6 行
不添加任何 prompt 中未出现的新机制、论文名、数字、年份

Aspect ratio: 16:9.
```

## 关键升级对比(给你看 verify 是否对齐)

| 旧版机制 | 新版机制 | 主要变化 |
|---|---|---|
| Skill Library (Voyager 代码) | **Anthropic-Style Skills**(SKILL.md) | 跟 Claude Code 格式对齐,可互通,不再 Minecraft 特化 |
| Failure Reflection (Reflexion) | 并入 GEPA(反思 = prompt 进化输入) | 不再单独机制,反思自然语言成 GEPA 输入 |
| Prompt Self-Tuning (DSPy MIPROv2) | **GEPA**(natural-language evolution) | 性能 +10-20%,代价 35× 少 |
| Episodic Memory (Generative Agents) | **Mem0**(2026-04 升级版) | 装 SDK 不造轮子,有 entity linking + temporal reasoning |
| — | **Skill Graph Self-Improvement**(ASG-SI 2025-12) | 全新加,把 skills 组织成可审计 DAG |

## 关联

- `apps/server/polynoia/storage/models.py` — `agents.system_prompt` 已 ready 版本化
- `apps/server/polynoia/mcp/tools.py` — audit.jsonl 数据源
- `apps/server/polynoia/sandbox/_core.py` — timeline.jsonl 数据源
- 前置图:`agent-context-composition.md` 讲 L1-L5 context model
- 前置图:`polynoia-data-schema.md` 讲存储分层

**外部 reference**:
- [Anthropic Skills blog](https://claude.com/blog/skills)(2025-10)
- [Mem0 GitHub](https://github.com/mem0ai/mem0)
- [Agent Memory Survey](https://github.com/Shichun-Liu/Agent-Memory-Paper-List)
- arxiv 2507.19457 (GEPA, ICLR 2026)
- arxiv 2512.16301 (Adaptation of Agentic AI survey)
- arxiv 2512.23760 (ASG-SI)
- arxiv 2512.17102 (RL for Self-Improving Agent with Skill Library)
- arxiv 2602.02474 (MemSkill)
