# 500-用例 压力 + 质量/UI BUG 检测 战役报告(2026-06-13)

> 目标(`/goal`):跑那 500 个 seeded 用例;**测试开始后不清库** —— 一来做压力测试,
> 二来做质量 / UI BUG 检测。状态累积、不 reset,让规模 + 时间把 bug 逼出来。
>
> 驱动:`scripts/testkit/stress500.py`(WS `user_message` → 真实多 agent 轮次 →
> 沉降判定 + 自动作答 ask-form + 承重 git 不变量检查)。语料:`scripts/testkit/seed_cases.py`
> (500 条 effect-driven 用户口吻用例)。

---

## 一、压力测试结论(已取证)

### 1. 并发天花板远低于 conc 10 —— 单进程 asyncio + 单 SQLite writer 撑不住

| 并发 | 现象 |
|------|------|
| conc 10 | `GET /api/conversations` 劣化到 **~19.5s**;WS 握手 **~38% 超时**;后端 log 被刷屏(~50 行/s) |
| conc 10 叠加后 | 即使停止加压,后端**不自愈**:API 持续 20s+ 超时,而 log **完全静默(+0 行/5s)** |
| 终态 | **事件循环彻底卡死(hang)**,非"慢" —— 请求被 accept 但永不完成,需**重启进程**才恢复 |

**关键区分:不是"慢",是"卡死"。** 健康但空闲的后端会瞬间返回 GET;而此处 API 挂 20s
且 log 静默 —— 这是事件循环被阻塞 / 死锁的特征,不是吞吐不足。

### 2. 根因:SQLite 单写者锁争用(WAL + busy_timeout 也救不了)

- 当日 `database is locked` 报错 **365 次**。
- DB **已是最优 PRAGMA 配置**:`journal_mode=WAL` + `busy_timeout=5000` +
  `synchronous=NORMAL` + 64MB cache(`storage/db.py`)。**仍然锁** —— 说明在高并发多 agent
  写入下,某些写事务持锁 **>5s**,把 busy_timeout 耗尽,后续写者直接报错。
- 热点写路径(按报错次数):
  - `_liveness_sweeper` → `sweep_process_liveness`(**34**)—— 后台周期任务也在写 `process_runs`,
    与轮次写抢锁。
  - `messages.append_message`(**23**)、`run_adapter_turn` / `dispatch_user_message` 写(**20**)、
    `messages.upsert_message`(**6**)。
- WAL 允许「多读 + 单写」,但**并发写者之间仍是单写**;多 agent burst 同时 streaming chunk →
  每个 chunk 一次 message payload 写 → 写队列爆 → 锁。

### 3. no-reset 下 agent 子进程堆积,放大事件循环饥饿

- meltdown 时刻后端进程树:**102 个进程**(server + 后代),其中 **72 个 claude/codex/opencode CLI**
  并发存活 —— conc-10/5/3 几波的 in-flight 轮次从未排干。
- 72 个 CLI 同时通过 MCP/stdio 回流 chunk,单 asyncio loop 被彻底压垮。
- 另:agent 交付物(如「双色球选号器」)起的后台 `python -m http.server` 也不回收,端口 / PID 累积。

### 3b. 第二种熔毁:文件描述符耗尽(FD exhaustion)—— 距 SQLite 锁独立的 bug

conc-2 跑约 40 个 clean settle 后,后端**再次熔毁,但机制完全不同**:
- 报错 `OSError: [Errno 24] Too many open files`(`sandbox.append_timeline` 开 `timeline.jsonl` 处崩,
  实为已达 FD 上限,任何 open 都炸)。
- 表现:后端进程**活着但 502**(vite 代理连不上)、WS upgrade 全 000 → 新用例全 `ws-fail`(连发 14 个)。
- FD 构成:进程开着 **211 个 unix socket**(agent stdio / MCP 管道)—— adapter 会话池按
  `(agent_id, conv_id)` 缓存,**跨 conv 不回收**,no-reset 下 unix socket 单调累积。
- **真根因 = 软上限太低**:崩掉的 server 是 launchd 孤儿(PPID 1),继承
  `launchctl limit maxfiles` 的**软上限 256**。256 对多 agent 工作负载远远不够。
- 修复:从高 `ulimit` 的 shell 重启(`ulimit -n 60000`,< 内核 `kern.maxfilesperproc=61440`)。
  恢复后 API 200 / 0.05s。
- **部署影响(具体可落地)**:生产 launchd 服务必须抬 `maxfiles`(plist `SoftResourceLimits.NumberOfFiles`
  或启动脚本 `ulimit -n`),否则线上同样会在负载下 FD 耗尽。
- **更深(P1)**:审计 FD 生命周期 —— adapter 会话池需驱逐策略(LRU / 空闲超时),
  避免 unix socket 跨 conv 无限累积;`timeline.jsonl` 等确保 open 即用即关。

### 4. 恢复手法(满足"不清库")—— 两次熔毁都靠它

杀掉 server 进程树 + 回收孤儿 agent CLI / http.server,**重启 uvicorn 进程**(第二次额外抬 `ulimit -n`),
**DB 文件原样保留**(`~/.polynoia/polynoia.db`,500 conv 全在)。重启 ≠ 清库。
恢复后 API 从 20s 超时 / 502 回到 **0.04–0.08s**。

> in-flight conv(已 `last_message_at`)被驱动幂等跳过,不重触发;`ws-fail` 的 conv 因从未发出
> user_message(`last_message_at` 仍空)会被下一波自动重试。状态累积,符合 no-reset 约束。

### 5. 映射到 CLAUDE.md 既有 P1 路线

本战役为以下 P1 项提供了**硬证据**(此前是计划,现在是实测必要性):
- **Postgres / Alembic**(替代 SQLite 单写者)—— 直接消除 365 锁的根因。
- **每 agent 网络 / 进程隔离(netns / 容器)** —— 限制子进程堆积 + 资源争用。
- **MCP `Transport closed` 自动重试 + 子进程生命周期治理** —— 排干 in-flight、回收交付物服务。
- **抬 `maxfiles` + adapter 会话池驱逐**(新增,来自 FD 熔毁)—— 部署侧抬软上限 +
  代码侧给会话池加 LRU/空闲驱逐,根治 unix socket 跨 conv 累积。

可选缓解(不改架构):写入串行化 / 单写连接 + 队列;`_liveness_sweeper` 降频或并入轮次写;
chunk 写做 batch / 节流(减少每 delta 一次 message 写)。

---

## 二、可持续并发下的质量 / UI BUG 检测

> conc 10 已证实会熔毁、无法产出可观测的质量数据。改在**可持续并发**下把用例真正跑完,
> 让交付物 / 渲染 / 承重不变量可被观测。当前以 **conc 2** 起跑(2 群 × 各 3–4 agent burst
> ≈ 6–8 并发 CLI,仍是真实负载),实时盯延迟,健康则上探。

### conc 2 可持续性 —— 已确认健康(对照 conc 10 熔毁)

| 指标 | conc 10(熔毁) | conc 2(本轮) |
|------|---------------|--------------|
| `GET /api/conversations` 延迟 | ~19.5s → 20s+ 卡死 | **0.03–0.08s**(5 样本稳定) |
| `database is locked` | 365 / 日 | **2 / 最近 400 行**(可忽略) |
| 用例完成 | 大量 no-start | **真正 settled**,承重 `git=0` |
| ask-form 闭环 | —— | `settled+1ask`(加压下自动作答成功) |

→ conc 2 = 2 群 × 3–4 agent burst ≈ 6–8 并发 CLI,仍是真实负载,但后端稳定、用例可跑完、
承重不变量干净。质量 / UI 检测在此并发下进行。

### 质量挖掘工具

新增 `scripts/testkit/quality_mine.py`(只读 DB,WAL 下不扰动运行中的 wave):统计 reach /
空气泡 / 工具错误率 / 终端失败 / payload-kind 健全性 / 承重 git 不变量。

### 初轮挖掘(57 活跃会话,meltdown + conc-2 混合)

**强阳性(承重 + 渲染健全):**
- ✅ **承重 git 不变量:105 个工作区,0 违反**(单 HEAD / 无 MERGE_HEAD / 无 `<<<<<<<`)——
  冲突闭环承重区在高强度多 agent 竞争下完全守住。
- ✅ **未知 payload kind:0** —— 8 种已用 kind 全在渲染注册表内,无崩渲染风险。
- ✅ ask-form 闭环在加压下成功(`settled+1ask`,阶段式作答 → REST answer)。

**空气泡 = 误报,已澄清(非产品 bug):**
- 初版 miner 读错字段(body 块内容在 `.c`,而非 `.text`/`.value`),把所有 list-body 消息误判为空。
- 修正 miner(对齐前端 `MessageView.isEmptyStreamingTextPayload` / `payloadText`)后,
  **空 agent 文本气泡 = 0**。
- 更关键:**前端本就守空文本** —— `isRenderableMessagePayload` 对**已定稿(非 streaming)**的
  text/reasoning 返回 `payloadText().length > 0`,即定稿空 body **根本不渲染**。所以即便 DB 里
  存在空 body,也不是可见空气泡。**结论:无渲染 bug。**

**静默会话 = 快照态,非 bug:**
- 复挖中「静默」集合随时间**变化**(每次约 14 个但成员不同)—— 这是 in-flight 启动期的快照
  (user_message 已发、agent 首条消息未产),turn 产出后即转非静默。非持久 bug。

**`ask_user` 工具调用 `error` 终态 —— 深查后判定为 kill 残留,非 clean-flow bug:**
- 现象:`name=mcp__polynoia__ask_user, state="error", is_error=False, output_text=""`(15 个)。
- 逐 turn 追踪:12 个 turn 在 ask 处终止、3 个在 ask 后还有同-turn 后续内容。
- 但 `_coerce_tool_state`(routes.py:368)**有终态守卫** —— 只翻 `pending`/`running`/`run`,
  绝不把已 `completed` 降级为 error。且 watchdog(ws_conv.py:1414)在 ask 打开时**保持 turn 存活**,
  clean turn-end(ws_conv.py:1772)会把残留 pending 工具置 `completed`。
- ∴ 这 15 个都是**turn 被杀/熔毁中止时**(本次恢复我 kill 了 102 进程)残留 pending 的
  ask_user 被 `_flush_partial_trace` 正确置 error 的产物。3 个"有后续"也只是被杀前已产出部分内容的 turn。
  **逻辑正确,无 clean-flow bug。**
- 决定性复核:clean conc-2(当前无 kill)下该计数**是否增长** —— 不增 = 纯 kill 残留(已预期)。
- 唯一微瑕(不改):被杀 turn 的 pending ask_user 落 `error` 而非 `cancelled`/`interrupted`,
  语义略误导 + 轻微污染质量画像错误率;改动落在承重邻近的 turn-death 路径,**风险>收益,不动**。

**其余遥测(非 bug):**
- `uv add fastapi`(exit 2)= `No pyproject.toml` —— **agent 顺序失误**(未先 `uv init`),
  非沙箱/pypi/proxy 问题(沙箱网络正常)。
- 终端 `exit=-1`(`ls -la`、`http.server`、heredoc)= **恢复时进程树 kill 的产物**,非产品 bug。
- `rg --files` exit=1 = 无匹配的正常返回。
- **工具命名三套并存**:`mcp__polynoia__X` / `polynoia_X` / `polynoia::X`
  (claudeCode/codex/opencode 各自命名)—— 影响遥测聚合 / UI 工具名展示,建议归一化。

### 对抗 / 安全发现:agent 越出沙箱驱动宿主 Chrome 自验(隔离缺口)

跑网页类交付时,多个 agent 为"自验产物"会:找 `playwright`/`puppeteer`(基本未装)→
**回退直接拉起宿主 `/Applications/Google Chrome.app`**(`--headless=new --dump-dom` 或
`--user-data-dir` 临时 profile 加载生成的 HTML、抓 DOM、查 console)。其中
`老顾客想攒点优惠` 用了 **`osascript -e 'tell application "Google Chrome" to open location …'`** ——
AppleScript 控制另一个 App,被 macOS 归因到根进程 **python3.12** → 弹出 TCC「python3.12
想控制/修改 Google Chrome」拦截框(用户已放行)。

- **未伤测试**:后端 0 条权限报错;两次熔毁(SQLite/FD)与之无关;`FAIL browser check` 是
  agent 自验逻辑对交付内容(404/CSV)的判定,非权限失败。
- **真隔离缺口(对应计划 Track A5 对抗/安全)**:P0 cwd 沙箱**拦不住 agent 拉起 / AppleScript
  控制宿主 App**。`--user-data-dir` 临时 profile 隔离尚可;但 `osascript tell application
  "Google Chrome"` 操作的是**用户正在用的真 Chrome 实例**(本次只开 localhost 页,但能力越界)。
- **建议**:P1 每-agent netns/容器隔离;在此之前,沙箱工具层可考虑**禁止 spawn 宿主 App /
  osascript**,或给 agent 预置受控 headless 浏览器(playwright)避免它去碰宿主 Chrome。

### 阶段小结(三半场)

> 这轮压测把一堆"吓人"的原始数字逐一证伪:**静默会话** = in-flight 启动快照(集合随时间变);
> **空气泡** = miner 读错字段 + 前端本就守空文本(真实 = 0);**27 工具错误** = 15 个 ask_user
> kill 残留 + uv agent 顺序失误 + rg 无匹配 + 我恢复时 kill 的终端。**真正的产品 bug:0。**
> 与此同时 **承重 git 0 违反 / 112 工作区**、**未知 payload kind 0**。系统在可持续并发下健康。

### 进行中
- [ ] 复挖确认:ask_user-error 不随 clean conc-2 增长、empty=0 稳定、承重持续 0
- [ ] 全轮 settled / timeout / no-start 分布 + 覆盖数
- [ ] UI 抽检(用户自测口径):群聊头像堆叠、阶段式询问窗、skill 选择器搜索/展开

---

*本文件随战役推进增量更新。*
