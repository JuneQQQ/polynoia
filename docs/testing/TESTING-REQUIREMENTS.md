# Polynoia 测试要求(测试战役交接说明)

> 本文件把贯穿本轮协作的**测试要求**集中、清晰地写下来,作为交接依据。
> 原始会话记录(raw transcript)在 `/data/lsb/polynoia-transcripts/287eeb86-….jsonl`,
> 内容庞大;**测试要求以本文件为准**。
> 目标条件(/goal):**「全量测试完毕,且代码优化完毕」** —— 两者都满足才算完成。

## 0. 运行环境(硬性)

- **全部测试在 `dev@10.2.255.109` 上做**(ssh 别名 `a100-xidian` 即此机)。
- **项目必须以 `dev` 用户启动**,否则 agent 账号凭证可能缺失(claude/opencode CLI 登录态在 dev 家目录)。
- 访问:`ssh root@10.2.255.109` →(免密)`su - dev -c '<cmd>'`。
- 仓库:`/data/lsb/polynoia`,分支 `main`。后端 `uvicorn polynoia.main:app` :7780,
  启动需 `ulimit -n 1048576`(默认 1024 会在高并发 FD 饿死)、**不要 `--reload`**、`setsid`、
  代理 `http(s)_proxy=http://127.0.0.1:7890`。
- DB:`/home/dev/.polynoia/polynoia.db`(seed/清库前先 `.bak-<ts>` 备份)。

## 1. 全量功能/压力测试

- **跑满 500 个用例**(`scripts/testkit/seed_cases.py` 生成,`stress500.py` 驱动)。
- **同时进行的任务数 ≤ 10**。经验阈值:conc-10 会击穿(认证洪水 / 后端融化);
  **conc-4 干净**;但真正的天花板是 **Anthropic Max 滚动速率限制(累计吞吐量,不是并发数)**,
  所以正解是 **adapter 退避重试**(已实现:429/401/凭证/quota → 指数退避 `5,15,30,60,120s` ×5),
  而非靠调并发。建议 `--conc 3 --batch 3 --per 800` 让退避吃掉残余限速。
- **agent 自我验证时不限制行为,让其自由探索**(交付物自检:能否 build、后端能否起、API 是否真响应)。
- **每批次测完务必干掉产物**(`reap_artifacts`:只杀交付物 server 进程,**绝不杀 agent 运行时**
  claude/codex/opencode/.venv/uvicorn/bwrap;并清 npm-cache/node_modules/dist 等磁盘大户),否则撑爆内存/磁盘。
- **诚实判定成败**:不能把「沉降/安静」当成功 —— 出错卡也会变安静。必须逐 conv 读 error card 统计 OK/ERR(auth)。

## 2. 跨模型基准(13 个 opencode-go 模型)

逐个测以下模型的**任务完成质量(尤其是弱模型)**与**上下文超限时的处理和效果**:

`kimi-k2.7-code, kimi-k2.6, glm-5.1, glm-5, minimax-m3, minimax-m2.7, qwen3.7-max,
qwen3.7-plus, qwen3.6-plus, deepseek-v4-pro, deepseek-v4-flash, mimo-v2.5, mimo-v2.5-pro`
(`kimi-k2.5` 服务端报错,跳过)。

- 驱动:`scripts/testkit/cross_model.py`(对每个模型跑 `run_benchmark.py` 用例集);
  先 `ensure_contacts()` 串行预建联系人(避免并发竞态),用例为主序。
- 上下文超限探针:`scripts/testkit/overflow_probe.py`(needle-in-haystack,**尚未实跑**,需执行验证)。
- 既有结论(参考):minimax-m3 最好(~100%),deepseek-v4-pro 最弱(~50%),`csv_upload_dashboard` 最难。

## 3. 多端(移动 + 桌面)

- 本地测移动端 + 桌面端,重点 **平台特有边界** + **多端同时在线行为**(广播扇出、并发发送、并发审批 ask-form 竞态)。
- 驱动:`scripts/testkit/multi_client.py`。前端平台切换:URL `?platform=mobile|desktop`。
- 桌面 Tauri / 移动 Capacitor 复用同一份 `apps/web` 构建。

## 4. 前端性能

- **消息列表懒加载 / 长列表卡顿**、会话列表卡顿需优化。
- 已做:Sidebar 会话行 `content-visibility:auto`(reflow 122ms→8.7ms,commit aad5fd9)。
- `@tanstack/virtual` 当前**未安装**,虚拟列表方案需另评估。

## 5. 必修渲染 Bug(用户从浏览器实见)

1. **草稿残留**:正在运行中,初始化时写进聊天框的草稿数据仍然存在
   → 已修:用户消息持久化后清空 `draft_text`(commit 2da4c25)。
2. **时间序倒置**:有一个「正在进行中的写入」,但其下方已经有文字
   (违反时间先后,是早期 dispatch-lingering bug 的复发)→ 需在**干净数据**上复验
   (此前只复现 2 例,且与被 reaper 误杀的轮次相关,reaper 已修)。

## 6. 代码优化(/goal 的另一半,已完成)

均已 commit 到 main:rate-limit 退避重试(be801d1 + produced-guard 013f488)、
草稿清空(2da4c25)、reaper 只杀交付物 server + 磁盘清理(fc499e3 / cea647c)、
Sidebar 性能(aad5fd9)、诚实 error-card 判定(6413ed1)、自适应并发 runner(23b9e76)。

## 7. 验收(怎么算「全量测试完毕」)

- 500 用例在干净额度下实跑一遍,产出**诚实通过率**报告(区分 OK / ERR-auth / 真失败)。
- 跨模型矩阵成形(13 模型 × 用例,含弱模型质量 + 上下文超限表现)。
- 多端 + 渲染 Bug #2 在干净数据上复验通过。
- 战役报告落 `docs/testing/`(对应任务 #82 / #87)。

## 8. 当前状态(2026-06-14 22:1x)

额度已恢复(PONG OK)。但 **109 当前 DB 是从 Mac 传过来的 505 条对话记录(live :7780),
用户明确「先不跑测试」**。**跑全量需先决定是否备份+清库重新 seed —— 未经确认不要清掉这 505 条记录。**
