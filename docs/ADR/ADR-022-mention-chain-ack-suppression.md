# ADR-022 — 群聊 @mention 接力的「致谢回弹」抑制 + 收敛协议

**日期**:2026-06-10
**状态**:Accepted
**相关**:[ADR-015](ADR-015-closed-loop-collaboration.md)(闭环协作 recall/report/critic)、[ADR-017](ADR-017-forced-orchestrator-role.md)(群协调器自启用)、[ADR-014](ADR-014-handoff-contract-and-shared-memory.md)(交接契约)

## 背景 / 问题

群聊里 agent 在正文 @ 队友会触发一次「链式派发」(`run_adapter_turn` 末尾扫描 `mentioned` → 为每个目标再 spawn 一轮),由 `_MAX_MENTION_CHAIN_DEPTH` 兜底防失控。这是 @ 路由协作的正常机制,但实测暴露一个收敛缺陷:

**产物交付(present)之后,agent 之间会用纯寒暄 @ 来回弹。** 通宵回归后,用户报告「调一两步工具就轮到下一个人回答」。Playwright 抓该会话尾部 23 行 DOM:`burstCards:0`、全部 `inBurst:false`、全是**零工具**的「思考 + 一句话」互相 @ 回去——

```
文澜 →@阿核「文档已核对完毕」 → 阿核 →@数擎「收到 backend ok」
   → … → ⚠️「@提及链路深度达到上限 5」 → present 卡之后又来一轮:
文澜 →@阿核「感谢验收反馈」 → 阿核 →@文澜「已记下」 → 文澜 →@阿核「stand-by」
   → ⚠️ 又撞到深度上限 5
```

两段接力各自一路滚到深度上限才停,尾部全是无进展的寒暄刷屏。根因:**深度上限是唯一收敛闸**,而「无内容致谢」本身从不被识别,所以每条「收到/感谢/已就位/确认闭环」都白白再 spawn 一轮。这不是简单的调参问题,而是**多 agent 会话里「完成信号」与「致谢」如何收敛**的协议级决策。

## 决策

三处协同改动,把致谢接力在源头掐断,而不动真·交接:

1. **深度上限 5 → 3**(`routes.py:_MAX_MENTION_CHAIN_DEPTH`)。仍容「问→答→一次跟进」;5 只是让寒暄多滚几轮才停。

2. **「致谢回弹」抑制**(`routes.py:_is_bare_ack_bounce`,接入 `ws_conv.py` 的 `_to_spawn` 构建处)。一回合**零工具 + 零 diff**(只思考 + 文本)且 @ 回**刚刚 ping 自己的那个人**(`target == parent_agent_id`)→ 判为无内容致谢,**不再 spawn**。放行三类:真交接(`turn_did_work=True`,如先交了 diff 再 @阿核)、新发问(@ 的不是刚 ping 自己的人)、根回合(无 parent)。discussion 综述不受影响——它经 `inflight==0` 路径回到 seeder,从不靠逐条 @ 回弹。

3. **present 回合不接力**(`ws_conv.py:_turn_presented` 并入 `_skip_chain`)。一回合调过 `present`(产物已交付用户)即视为终态,其尾随的 @mention 一律不 spawn。真·下一阶段走 `dispatch`(burst),从不是裸 @,故 present 回合永不需要链式接力。

## 不选的方案

- **只把深度上限调更小**:治标。深度只约束单条线性链,寒暄仍会在 ≤cap 内刷屏;且过小会误伤合法的「问→答→跟进」。
- **硬门所有「致谢类」回合(按文本判断是不是寒暄)**:要正则/LLM 判「这句是不是致谢」,脆弱且易误杀真信息。改用结构信号(本回合有没有干活 + 是不是 @ 回 pinger),零语义猜测。
- **LLM 仲裁「该不该继续接力」**:每条 @ 加一次判断调用,慢且贵,收敛性还不确定。

## 代价 / 风险

- **误伤**:一回合没干活、却想 @ 回 pinger 问个澄清问题(如 worker 反问协调器「你指 auth 测试还是全部?」)会被抑制。**缓解**:该文本仍**落库可见**,协调器下一轮 advance/dispatch 会在历史里看到;只是不自动**立刻**再 spawn 一轮。实测此类极少(worker 多是干活而非反问协调器),相比寒暄刷屏的实害可接受。
- **与 burst 收尾路径正交**:burst 完成 → merge → 协调器验收轮(`need_continue`)是独立机制,不走 @ 链;本决策不影响它。

## 验证

- 纯函数 `_is_bare_ack_bounce` 单测 4 例(抑制回弹 / 干活后放行 / 新发问放行 / 根回合放行)+ 既有 mention/dispatch 测试全绿(`tests/api/test_mention_routing.py`)。
- 同一会话两次 live 复验(Playwright 发真实小需求 → settle → census DB):
  - Run 1(仅改动 1+2):正文 **0** 深度通知(旧版同类尾部撞 depth-5 两次);残留=present 回合多 @ 一次 → 末尾 2 条 ack(被 depth-3 截断)。
  - Run 2(3 全上线):制图 一气做完 2 diff + 5 bash 构建/自测 + report(真·工作段);**全程 depth-cap=0、ack-relay=0**;present 回合的 @制图 被静默抑制;协调器自动进验收轮 → present 收口。
- 详见 [docs/sessions/2026-06-10-pingpong-and-process-lifecycle.md](../sessions/2026-06-10-pingpong-and-process-lifecycle.md)。
