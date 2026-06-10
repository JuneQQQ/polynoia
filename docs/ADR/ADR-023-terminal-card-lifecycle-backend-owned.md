# ADR-023 — 终端卡生命周期由后端权威接管(而非创建它的 MCP 子进程)

**日期**:2026-06-10
**状态**:Accepted
**相关**:[ADR-013](ADR-013-role-based-mcp-tools.md)(分级 MCP 工具)、[ADR-003](ADR-003-workspace-shared-git.md)(沙箱/worktree)

## 背景 / 问题

agent 的 `bash` 工具会把终端快照(节流心跳)POST 到 `/api/conversations/{id}/terminal-card`,绘制聊天里的「终端卡」;长跑命令(绑监听端口)自动升级为后台,并在 `process_runs` 表登记。终端卡的 `running` 状态本来由**创建它的 MCP 子进程**负责翻成 `running=false`——两条路径:命令在阻塞回合内结束(回合末 coerce),或后台监视器发现进程退出。

问题:**MCP 子进程是会死的**(回合结束、CLI 在长回合中重启 MCP stdio server、OOM、kill)。它一死,这两条「关卡」路径都断了,于是:

- [BUG-1] 大量 blocking 终端卡永远停在「运行中」(命令其实早完成)。
- [BUG-2] 自动转后台的 uvicorn 卡谎报 `exit -1`(进程其实还活着——监视器在 CancelledError 上撒了谎)。
- 乱序快照竞态:一条迟到的 `running=true` 旧快照盖掉了 `running=false` 终态 → 卡复活成「运行中」。
- 后端重启后:上一实例留下的「运行中」卡无人收尾(MCP 早没了,`process_runs` 被标 killed 后连存活探测都跳过它)。

**核心张力**:卡片由 MCP 创建,但 MCP 朝生暮死;而后端进程是持久的、且**拥有 pgid**。谁该是终端卡状态的 system-of-record?

## 决策

**后端是终端卡生命周期的权威**;MCP 只负责「报告快照」,不再被信任为「宣告终态」。四条后端侧机制:

1. **单调 `seq` 快照守卫**(`routes.py:post_terminal_card`):快照带单调 `seq`;拒绝 `seq <= prev_seq` 的乱序/迟到快照;**绝不让 `running=true` 盖掉已 `running=false` 的终态**;绝不把已有输出缩回空。根治「卡复活」竞态。
2. **诚实的后台监视器**:`_monitor_background` 只在 `proc.returncode is not None`(真退出)时才发终态;CancelledError 上发**屏蔽的、诚实的** `running=false, exit_code=-1, final=true`,不再谎报存活进程已退出。
3. **30s 存活清扫器**(`main.py:_liveness_sweeper` → `repo.sweep_process_liveness`):后端持有 pgid,周期性 `os.kill(pgid,0)` 探活——死进程闭卡 + **重广播**修正后的卡(开着的客户端无需刷新即收敛),活进程刷心跳。彻底解除「卡片寿命绑定 MCP 进程」。
4. **启动 reap**(`main.py` lifespan):重启后上一实例的一切都不可信。`reap_stale_process_runs` 把 starting/running 的 `process_runs` 标 killed(清右栏面板);`reap_orphan_terminal_cards` 扫所有 `kind="terminal" running=true` 的消息,闭成 `running=false`(保留真 exit_code,否则 -1=未知)——**它独立于 process_run,因此也能收掉「process_run 已被上一次 reap 标 killed、但卡还运行中」的漏网卡**。

**一律 DB-only,启动 reap 不发 OS 信号**:重启后一个后台 dev-server 可能仍在 OS 层运行,但它的 pgid 可能已被复用,贸然 killpg 会误杀无关进程。卡片诚实地标成「跨重启失联(exit 未知)」,真正的端口回收交给 `reset.sh` 的 reaper(下次 reset 时按 pgid 杀)。

## 不选的方案

- **只靠 MCP 发终态快照(加重试)**:MCP 死在终态快照之前就永久卡死;治不了崩溃窗口。
- **启动时 killpg 所有旧 pgid**:pgid 复用风险——可能杀掉一个恰好复用了该号段的无关进程。否决(见上「DB-only」理由)。
- **前端拉取时 coerce 终端卡**(像 tool-call/burst 那样):正常运行中的后台服务卡 `running=true` 是**真的**,前端无 pgid 无法区分「活的后台服务」vs「死掉没收尾」,盲 coerce 会误杀活服务卡。后端(持 pgid)才是唯一能正确判定的地方。

## 代价 / 风险

- **跨重启的活后台服务卡显示为「已结束(exit 未知)」**,尽管 OS 进程还活着。这是刻意取舍:重启后平台已对该进程失去跟踪(pgid 不敢复用),显示「失联结束」比永久「运行中」诚实;且该卡所属回合早已结束。
- **存活清扫器探测 pgid 有 30s 粒度**:死进程的卡最多 30s 后才闭——可接受(非交互路径)。

## 验证

- 乱序竞态、监视器诚实终态:回归案 0 卡死(对照旧版大量卡死)。
- `reap_orphan_terminal_cards` 单测(`tests/storage/test_process_runs.py`):覆盖 process_run 已 killed / 无 process_run / 已有真 exit_code(保留)/ 非终端卡(不动)四种;实测后端重载后 4→0 张卡死终端卡。
- 通宵 20 案:终端卡全闭合(除真正运行中的后台服务)、`exit -1` 谎报=0。详见 [docs/sessions/2026-06-10-pingpong-and-process-lifecycle.md](../sessions/2026-06-10-pingpong-and-process-lifecycle.md) 与 [docs/sessions/2026-06-overnight-e2e.md](../sessions/2026-06-overnight-e2e.md)。
