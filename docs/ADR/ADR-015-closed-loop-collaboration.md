# ADR-015 — 闭环协作:recall / report / critic + 验收 nudge + 防卡死

> 状态:Accepted(2026-05-30)
> 关联:[[ADR-013-role-based-mcp-tools]]、[[ADR-014-handoff-contract-and-shared-memory]]
> 调研:`docs/diagrams/reasoning-and-execution-decoupling.md`、RuFlo(ruvnet/ruflo)深研工作流

## 背景

ADR-014 给了协作的「写」侧:`dispatch` 锁契约 + `remember` 写共享记忆。但深研 RuFlo
后发现我们的协作仍有几处**不闭环**:

1. **派活是 fire-and-forget**——worker 干完只是 lane 翻 `done`,没有显式 ACK;
   orchestrator 的"验收"只能靠 history 猜,容易盲信"已交付"。
2. **共享记忆只在 turn 开头读**(L 层),worker 执行中无法查最新契约/队友产物。
3. **没有专职审查角色**——验收和实现混在 orchestrator 一个 prompt 里。
4. **静默卡死**:dispatched worker 若拿不到 adapter session 就 `return`,**不标 lane**
   → burst 永远等不到 `is_last` → 不合并、不收尾。

## 决策

四个 surgical 增量(都复用既有 `dispatch`/contract/conv_memory/burst 机制,**不重写**):

### 1. `report` MCP 工具 — 闭环交付确认
- worker 子任务结束**必须**调 `report{status, deliverables, contract_ok, notes}`。
- 服务端写成 `conv_memory` 的 `kind=artifact` 条目 → orchestrator 的 auto-summary turn
  经 shared-memory L 层**读回**每个人的自评 verdict,据此验收;且刷新不丢。
- **不给 orchestrator**(它消费 verdict,不对自己的 dispatch 报告)。
- worker spawn prompt 追加硬性收尾指令:"没 report 的产物按『未验证』对待"。

### 2. `recall` MCP 工具 — 执行中读 blackboard
- `recall{kind?}` GET `/api/conversations/{id}/memory`,worker **执行中**就能查
  最新契约 / 队友已记录的 decision+artifact,不必等下一 turn。
- **不做向量检索**(RuFlo 的 HNSW 明确超纲,见 ADR-014 代价节)——kind 过滤 + 子串足够。
- 所有角色可用(对称于 `remember`)。

### 3. `critic` 只读审查角色
- 新 `tool_role=critic`:只有 `read/grep/glob/recall/report`,**没有 write**。
- 可被 dispatch 成一条 burst lane,专门核对他人产物是否符合契约,产出 report verdict。
- 价值依赖 #1#2 落地后才完整(故排在其后)。

### 4. 验收 nudge 强化 + 防卡死
- summary nudge 明确要求:① 点名没 report 的 lane(按未验证处理);② 对自评
  `contract_ok` 的产物**用 read 抽查**,别盲信;③ 有 failed/未验证就**升级为问题汇报**,
  不准笼统说"已完成"。
- 修 adapter-unavailable 早退**不标 burst lane** 的洞 → 现在所有失败出口都翻 `failed`,
  burst 一定能到 `is_last`(合并 + 收尾)。

## 不选 / 代价

- **不做强制 schema 化的 report**(verdict 仍是文本)——保持 surgical,先把闭环跑通。
- **不做硬 merge-gate**(失败 lane 阻断合并)——风险高(改 user-facing 收尾);改为
  nudge 软门:summary 必须显式点出失败/未验证,而不是物理阻断合并。
- **不做 per-task 自动重试 / 依赖 DAG / burst watchdog**——已在 RuFlo 设计里,列为后续
  (`retry_count` 字段已存在待接;deps 需新 ADR;watchdog 需谨慎接入已验证的 burst 生命周期)。
- 代价:`report` 是 prompt nudge 不是强约束——agent 可能忘调;用"未验证"显式兜底,
  把缺口暴露而非隐藏。

## 影响文件

- `mcp/tools.py`:`_RecallTool` / `_ReportTool` + `ROLE_TOOLS`(加 `critic`,recall 全角色,
  report 给 worker 角色)。
- `api/routes.py`:`GET /memory`(recall)、`POST /report`、worker spawn report-nudge、
  summary verify/escalate-nudge、adapter-unavailable 标 lane failed。
- 验收:`tests/mcp`(22)+ 全量 pytest(119)绿。
