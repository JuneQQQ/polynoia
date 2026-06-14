# 压力测试战役报告 — A2 真实并发竞争(2026-06-13)

> 真实·多角度·高强度测试体系的第一份产出。承重区(冲突闭环 / `_merge_burst_to_main`
> / `workspace_merge_lock`)第一次被**真实多 agent 并发**压,而非合成 git 状态。
> 复现脚本:`scripts/testkit/contention.py`。取证:`.tmp/contention-r1-forensics.json`。

## 🔴 BUG-A2-1(高):resolve 后贡献分支 worktree 未推进 → 重复合并死循环

**严重度**:高(功能性死循环 + 冲突卡无限堆积;但 main 不损坏,git 不变量全过)。
**发现方式**:`contention.py` 第 1 轮 —— orchestrator(阿核)用 `dispatch` 并行派 3 个
真 agent(QA / 全栈工程师 / 前端实习生)同时改**同一个** `shared.md` + `shared.py`。

### 现象
- git 不变量**全过**(`git=0`):main 单 HEAD、无 `MERGE_HEAD`、无 `<<<<<<<` 残留,
  main 停在 `7b42da2`(三方齐全的正确终态)。承重合并没把 `.git` 搞坏。
- 事件流不变量**1 违反**:`INV12 conflict card not terminal (status='open')`。
- 取证流里 **8+ 张 `data-conflict` 卡全 `open`、两侧内容完全相同**,反复出现
  (conflict_id 01KV01QX… / 01KV01WC… / 01KV01X8… / 01KV01XQ… / 01KV01Y7… / …)。
- orchestrator 自己实时诊断(turn_events 原话):
  - seq371「resolve_conflict 报 resolved,但当前工作目录视图还停在合并前的版本」
  - seq575「这条分支显然陷入了**重复合并循环**(每次 resolve 都不更新该成员的 worktree base)」
  - seq632「平台不把 resolve 后的状态推回成员 worktree 的 base,所以下一次 merge 重试又撞同样冲突」

### 根因(代码层定位)
1. worker 分支合 main 撞冲突 → 开 conflict 卡(引用该分支),分支**正确地不 reset**
   —— `reset_worktree_to_main`(`sandbox/_core.py:413`)文档明确:open/resolving 冲突
   引用的分支不能 reset,否则毁掉用户未选的那侧。turn-start sync(`ws_conv.py:1168-1178`)
   据此跳过。
2. `/api/conflicts/{id}/resolve`(`routes.py:2542`)→ `conclude_merge`(`_core.py:1795`)
   把分支**合进 main 并在 main 上提交**,标 `resolved`。**但 conclude_merge 只动 main,
   不动 branch / worktree**(读 1864-1895:commit 在 main,无 branch reset)。
3. resolve 端点成功分支(routes.py:2594-2611)**也没** reset 贡献者 worktree。
4. 该 worktree 仍停在 resolve 前 base → 分支仍 `ahead_of_main` → 下一次 drain 又把它
   merge → 又撞同样冲突 → 又开 conflict 卡 → orchestrator 又 resolve → ∞。

一句话:**resolve 推进了 main,却把贡献分支留在过期 base 上,于是它被反复重新合并、
反复重新冲突。**

### 为什么合成测试测不到
`test_conflict_flow.py` / `test_conflict_merge.py` 等 resolve **一次就停**,从不验证
"resolve 之后该分支还会不会被再次 drain"。只有真 agent 在真 burst 里持续活动 + 真
drain 循环才暴露。这正是"真实高强度"的价值。

### 修复(已实施 + 已验证)✅
`routes.py:resolve_conflict_endpoint`(🟢 `/api/conflicts/*` 自由区):resolve 成功
(`ok=True`、状态→`resolved`、且无其它 open 冲突引用该分支)后,在
`workspace_merge_lock` 内调用既有 `reset_worktree_to_main(...)` 把分支推进到新 main
→ `ahead_of_main==0` → drain 不再重合该分支 → 循环断。此刻冲突已 `resolved`,满足
helper 的安全前提(非 open/resolving)。

**验证**:
- 回归测试 `test_resolve_advances_branch_so_no_remerge_loop`(test_conflict_flow.py):
  resolve 前分支 ahead≥1 且 probe=conflict;resolve 后 **ahead==0 且 re-probe≠conflict**。
- 全冲突/合并承重套件 **47 测试全绿**(修复零回归)。
- **真 agent 复验**:`contention.py` round 1 = `git=0 event=0`(此前必现的循环消失;
  round 2 只 3 张**不同** conflict 卡,而非此前 8+ 张同内容狂刷 → 循环确已断)。
- CHARTER 合规:PR 点名 `touched resolve_conflict_endpoint — see conflict-closed-loop-CHARTER.md`。

## 🟡 BUG-A2-2(中,待评估):并发收敛后留下未解决的 open 冲突

**发现**:contention round 2(累积态)沉降后,**agents 全停(running 空)却留下 2 张
conflict 卡永久 `open`**(conflict-48c57… / 43b9ea…),orchestrator 没在结束前把它们
resolve 完。这**不是** A2-1 的循环(卡是不同的、不狂刷),是另一类:多 agent 收敛后
**残留未解决冲突无人兜底**。

**两种解读**(待定):
1. **产品 by-design**:未解决冲突就该留 open 等人处理 → 那 INV12「settled conv 无 open
   冲突」过严,应区分「真 bug 的 stuck」与「待人工解决」两态。
2. **真缺口**:orchestrator turn budget 用尽/判断停手时,没有「把未决冲突升级提醒用户
   或自动再 resolve」的兜底 → 冲突静默躺平,用户不知道要处理。倾向认为这是缺口。

**待办**:加「settle 时若有 open 冲突 → 显式升级(@用户 或 orchestrator 收尾再 resolve)」;
或给 INV12 增加「open-awaiting-human」合法终态。需产品决策。

## 🔧 Harness 校准(已知)
- contention 的沉降判定「45s 无事件」**过急**:round 2 一个 worker 任务仍 `run`、事件
  恰好静默 45s 时就判沉降去查 → INV3「task stuck」其实是误判(该任务现已 done)。
  → 沉降条件应加「无非终态 tasks/conflict 卡 或 更长静默窗口」。已记,下轮修 harness。

## ✅ 同时验证为健壮的
- **承重合并本身**:8+ 次真实并发合并 + resolve,main 始终单 HEAD、无半合并、无标记
  残留、停在正确终态。`workspace_merge_lock` 串行化有效。
- **冲突检测 + autofix**:真冲突被如实识别、内联、可 resolve(只是 resolve 后 base 未推进)。
- **事件日志**:643 事件 seq 严格单调无空洞,turn_events 取证链路完整可用(这次诊断
  完全靠它 + orchestrator 自述还原)。

## 顺带发现(低)
- 会话级 `running_agents` 在并发 burst 期间报 `False`,而事件流持续涌出 —— 会话级
  running 标志未反映 burst worker 活动。`contention.py` 靠"事件流静默 45s"兜底沉降判定。
  (待查:是否该把 burst 在跑也算 conv running。)

## 复现
```bash
apps/server/.venv/bin/python3 scripts/testkit/contention.py --rounds 1 --workers 3
# 失败时自动导出 .tmp/contention-r{n}-forensics.json(git 违反 + 事件不变量 + 事件流)
```
