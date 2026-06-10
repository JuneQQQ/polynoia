# 会话:@mention 致谢接力 + 终端卡生命周期 + 桌面端端口劫持

> **日期**:2026-06-10
> **触发**:通宵 E2E 之后,用户报告群聊「调一两步工具就轮到下一个人回答,逻辑上有问题」,并随后观察到「桌面端被启动的缺陷管理项目顶掉」。
> **结果**:3 个根因定位 + 修复,均带单测 + live 复验。对应 [ADR-022](../ADR/ADR-022-mention-chain-ack-suppression.md)、[ADR-023](../ADR/ADR-023-terminal-card-lifecycle-backend-owned.md)。

## 1. 「调一两步就换人」=@mention 致谢接力(不是 turn 被截断)

**取证**:Playwright 抓涉事会话 DOM,尾部 23 行 `burstCards:0`、全 `inBurst:false`,全是零工具的「思考+一句话」互相 @ 回弹;两段接力各自撞到「@提及链路深度上限 5」才停。即:**产物交付后,agent 之间纯寒暄 @ 来回弹**,每条都白白再 spawn 一轮。

**修复(三处)**:深度上限 5→3;`_is_bare_ack_bounce`(零工具回合 @ 回 pinger → 不 spawn);`_turn_presented` 并入 `_skip_chain`(present 是终态,尾随 @ 不接力)。详见 ADR-022。

**复验**:4 单测 + 同会话两次 live 跑——Run 2(全修上线)制图做完 2 diff + 5 bash 自测 + report,**全程 depth-cap=0、ack-relay=0**,present 收口干净。

## 2. 终端卡反复卡「运行中」=生命周期绑死在会死的 MCP 上

通宵已修一批(乱序竞态、谎报 exit -1、liveness sweeper),本次补上**重启缺口**:`reap_stale_process_runs` 只清右栏面板、且跳过已 killed 的 run,导致后端重启后聊天里的终端卡永久卡「运行中」。

**修复**:新增 `reap_orphan_terminal_cards`(启动时扫所有 `kind="terminal" running=true` 的消息直接闭卡,独立于 process_run,覆盖「process_run 已 killed 但卡还运行中」漏网)。实测后端重载后 **4→0** 张卡死卡。沉淀为 ADR-023:**终端卡生命周期由后端权威接管**。

## 3. 桌面端被「缺陷管理项目」顶掉=devUrl 端口错配 + agent 占用

**根因**:`apps/desktop/src-tauri/tauri.conf.json` 的 `devUrl` 硬编码 `http://127.0.0.1:5173`,但本地 web 早改到 **7788**(`vite.config server.port`)。桌面 webview 一直指向 Polynoia 根本不在的 5173;测试里 agent 跑 `npm run dev`(Vite 默认 5173)抢占该空端口,桌面就渲染成了它的项目。

**修复**:`devUrl` → `7788`(指向真 web);vite 加 `strictPort: true`(7788 被占即报错退出,绝不静默漂移到 7789 而把 7788 让给 agent)。生效需重启 `tauri dev`。更广义:host 服务用冷门端口(已是 7780/7788),agent 默认端口撞不到;彻底隔离(每 agent netns/容器)是 P1。本项判定为**操作性约束,代码注释足够,不单开 ADR**。

## 验证基线

后端 pytest:239 passed(含新增 `_is_bare_ack_bounce` ×4、`reap_orphan_terminal_cards` ×1),5 skipped,1 pre-existing 失败(`test_refresh_credentials`,与本次无关)。
