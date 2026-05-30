# api/ — LIVE 合并/burst 入口(冲突闭环承重区)

> 你在编辑 api。`routes.py` 含**唯一真实合并触发**,被「冲突闭环」(`feature/diff_dev`)挂钩。
> **改合并/burst/conflict 区之前**,先读 [`/docs/design/conflict-closed-loop-CHARTER.md`](../../../../docs/design/conflict-closed-loop-CHARTER.md)。无关端点不受影响。

🔴 **承重区(routes.py)— 改签名/调用时机/返回处理会炸**:
- `_merge_burst_to_main`(:1683)— ★唯一真实合并点,冲突闭环挂这里
- `_mark_burst_task`(:1599)/ `is_last`(:1611)— burst 状态机,触发合并 + 驱动 BurstCard;`is_last` 必须同步判定 + 立即 pop
- `_conv_bursts`(:71)— reg 键结构 `{payload,pending,orch,workspace_id,contract}`,TasksCard/summary/merge 都读
- dispatch drain(:2022)/ `dispatch_user_message`(:2239)/ `run_adapter_turn`(:1704)— burst 调度链
- `_broadcast_to_conv`(:166)— 所有 `data-*` 卡的 WS 全广播

⚠️ **pending-edit 轨道**(create :1068 / wait :1106 / decide :1131 / list :1161):冲突闭环**逐一镜像**它。**重构 pending-edit 必须同一 PR 同步改 conflict。**

🟢 自由动:`/api/conflicts/*` 端点(功能私有);其它无关端点随便改。
⚠️ `OrchestratorRuntime._maybe_run_merge_phase`(orchestrator/runtime.py)是**死代码**,别复活、别往上加。
