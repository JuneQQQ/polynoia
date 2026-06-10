# api/ — LIVE 合并/burst 入口(冲突闭环承重区)

> 你在编辑 api。`routes.py` 含**唯一真实合并触发**,被「冲突闭环」(`feature/diff_dev`)挂钩。
> **改合并/burst/conflict 区之前**,先读 [`/docs/design/conflict-closed-loop-CHARTER.md`](../../../../docs/design/conflict-closed-loop-CHARTER.md)。无关端点不受影响。

🔴 **承重区 — 改签名/调用时机/返回处理会炸**(注:真正的 burst/merge 引擎住在 **`ws_conv.py`**;`routes.py` 只剩共享注册表 + 广播。行号截至 2026-06-10,移动后请就近更新本表):
- `_merge_burst_to_main`(**ws_conv.py:786**)— ★唯一真实合并点,冲突闭环挂这里
- `_mark_burst_task`(**ws_conv.py:226**)/ `is_last`(**ws_conv.py:238**)— burst 状态机,触发合并 + 驱动 BurstCard;`is_last` 必须**同步判定 + 在首个 await 之前立即 pop**(asyncio 协作调度下这就是原子性的来源,勿加锁)
- `_conv_bursts`(**routes.py:175**)— reg 键结构 `{payload,pending,orch,workspace_id,contract,need_continue}`,TasksCard/summary/merge 都读
- `dispatch_user_message`(**ws_conv.py:2236**)/ `run_adapter_turn`(**ws_conv.py:892**)/ dispatch drain(ws_conv.py turn 末)— burst 调度链
- `_broadcast_to_conv`(**routes.py:476**)— 所有 `data-*` 卡的 WS 全广播
- `workspace_merge_lock`(**sandbox/_core.py:158**)— **已存在**,且已在 drain 处包住 commit→probe→conclude 全序列(进程内 asyncio.Lock;跨进程安全是 P1)。CHARTER §3.2「此锁尚未存在」已过时。

⚠️ **pending-edit 轨道**(create :1068 / wait :1106 / decide :1131 / list :1161):冲突闭环**逐一镜像**它。**重构 pending-edit 必须同一 PR 同步改 conflict。**

🟢 自由动:`/api/conflicts/*` 端点(功能私有);其它无关端点随便改。
⚠️ `OrchestratorRuntime._maybe_run_merge_phase`(orchestrator/runtime.py)是**死代码**,别复活、别往上加。
