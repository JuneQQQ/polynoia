# ADR-018 — 不采纳 RuFlo 式钩子框架,改用轻量提取

**日期**:2026-05-31
**状态**:Accepted
**相关**:ADR-014 / ADR-015(同样"学 RuFlo 思想、砍重型机器"的立场)· `docs/diagrams/ruflo-learnings.html`(③ 钩子机制)· `mcp/server.py`(`_call`)· `mcp/tools.py`(`_require_edit_approval`)

## 背景

研究了 RuFlo(`ruvnet/ruflo`,前身 Claude Flow)的**钩子机制**:`.claude/settings.json` 声明生命周期事件(`PreToolUse / PostToolUse / SessionStart / …`)+ matcher,调起 `npx ruflo hooks <name>` 这类 CLI 钩子(~14–17 个:`pre/post-task`、`pre/post-edit`、`pre/post-command`、`session-start/end`),做记忆持久化 / 自动格式化 / 协调 / 指标 / 校验。执行模型:校验类**阻断**(可 veto),观察类后台跑,钩子可返回 JSON 改/否决操作。

动机是"让我们的代码更优雅"——一个 workflow 把 `run_adapter_turn` 和 MCP 工具路径里的横切关注点都标了出来(审计、isError 包装、pending-edit 闸门、回调 POST、部分轨迹落盘、dispatch drain、闭环核验、burst 编排……)。

## 决策

**不为 Polynoia 建钩子框架(事件总线 / 注册表)。** 取 RuFlo 的**思想**(横切关注点不该抄进每个操作),用**最轻的工具**落地:函数提取 +(必要时)装饰器。

理由:
- **钩子框架解的是 RuFlo 的问题,不是我们的。** RuFlo 是**框架**,挂在**外部** Claude Code 上,要让**第三方**注册自动化。这三个前提我们都没有——我们是闭环 app,横切关注点固定且全是自己人写的。给"关注点已知且封闭"的系统装可插拔总线 = 过度抽象。
- **它把显式变隐式。** 读 `_call` 一眼看全;装总线后只看到 `run_hooks(...)`,真逻辑散到注册表,控制流非局部化。单团队闭源代码:**显式 > 聪明**。
- **它自带新复杂度**(注册表、触发顺序、veto/mutate 契约、总线自测),为的却是不需要可插拔的场景。
- **turn 路径有临界同步点**(`is_last` 竞态、per-agent 锁、stale-session 重试),包进钩子是给 live 命脉代码加风险换化妆品收益。
- 与 ADR-014/015 一致:"直接上 RuFlo 全套是过度工程"。**学思想,别搬机器。**

**实际落地(刻意保持小):**
- `mcp/server.py::_call`:把内联的六件事抽成 `_arg_preview / _result_summary / _wrap_result / _error_result`,`_call` 变成一条薄管线(role 门 → execute → audit/isError-map / on-error 信封)。零行为变化。
- `mcp/tools.py::_require_edit_approval`:收掉 edit/write/apply_patch **三处复制的** gate-then-reject 样板(各自的 lock/commit 因语义不同**保留显式**——apply_patch 是多文件、不锁单路径,硬塞装饰器反而制造特例)。
- **没动**的:`_callback_server`(已是共享 helper,4 个调用点是正当使用,非重复)、各 `git_commit` 的 message(edit/write/apply_patch 语义不同,本就该不同)、整个 turn 路径。

**会让我们改主意的唯一情况**:若 P1+ 把"用户/插件自定义 per-tool 自动化"列为**产品功能**,钩子总线就从投机抽象变成真需求,届时再建。只要不是已规划方向,它就是 YAGNI。

## 代价 / 取舍

- 横切关注点仍是**编译期固定**(改自动化要改代码,不能配置)——但这正是我们要的简单。
- 收益比预期小:细看发现代码本就相当 DRY,真正的提取只有 `_call` + 一个 gate helper。**这本身印证了结论**——它需要的不是框架,是两处小提取。

## 验证

- `mcp/server.py` ruff clean;`-k "tool or mcp or dispatch or gate"` 38 passed;全套 140 passed / 5 skipped(零行为变化,纯结构)。
