# ADR-009 — Manual mode 用 HTTP 长轮询而非 asyncio.Future

- **状态**:accepted
- **日期**:2026-05-28
- **相关**:`apps/server/polynoia/mcp/tools.py:_gate_via_pending_edit`,`api/routes.py:/api/pending-edits/*`,ADR-005

## 背景

Manual merge mode 要求"agent 调用 edit_file 时悬挂,等用户审批后再执行"。设计稿(merge-flow.html)说"那一个 coroutine 挂在 asyncio 事件循环上"。

问题:Polynoia 的 MCP 工具**跑在独立 stdio 子进程**(每个 agent session 一个),通过 `mcp__polynoia__*` 协议被 Claude SDK 调用。这个子进程**有自己的事件循环**,跟 FastAPI server 进程不共享。没法直接 `await asyncio.Future`。

候选方案:
1. **进程内 Future** — 把 MCP 工具改成 server 进程内 thread/coro,共享 Future map。**问题**:大改造,违反 MCP stdio 协议契约
2. **本地 socket / pipe** — MCP 子进程开个 unix socket 听 server 推
3. **HTTP 长轮询** — MCP 子进程定期 GET `/wait`,server 等到 status 变化才返
4. **Server-Sent Events** — server 推,MCP 收

## 决策

**HTTP 长轮询** + `httpx.AsyncClient`。

MCP `_EditTool` / `_WriteTool` / `_ApplyPatchTool` 在写入前调用 `_gate_via_pending_edit`:
1. POST `/api/pending-edits` 创建 row + 触发 WS 广播到 UI
2. 循环 GET `/api/pending-edits/{id}/wait?timeout=60`(server 端 0.5s 轮询 DB)
3. status 变 accepted / rejected / timeout 才返
4. 总超时 5 分钟自动 reject

## 为什么

- **最少机制** — 用现有的 HTTP infra,不引入 socket/SSE/IPC 框架
- **跨进程天然解耦** — MCP 子进程崩了 server 不影响,server 重启时 MCP 看到 504/connection refused fail-open 继续工作
- **可测** — `httpx.AsyncClient` mock 友好,5 个单元测试覆盖 auto/manual/accept/reject/transport-failure 路径
- **`httpx` 已是依赖** — 后端其它模块已用,零新增依赖

## 否则会怎样

走 Future / socket → 需要给 MCP 进程加 IPC 监听层 + 同步原语;复杂度上 3 倍,bug 来源更多。

## 代价

- 多 0.5s 的 polling 延迟(用户已经接受了"等审批"语义,这点延迟不可见)
- 长轮询期间 server 多一个 sleep 协程 + 每次 0.5s 一次 DB lookup —— sqlite 单连接 < 1ms,可忽略
- "进程崩了导致 pending 卡住" 由 5 分钟全局 timeout 兜底 → 触发 reject

## 反例(将来可能反悔的场景)

如果未来 Polynoia 的 agent 数 + 并发 manual gate 超过 ~100/s,长轮询会变成 server 端的 sleep 堆积。届时切 SSE 或 WS push。当前 P0/P1 量级远低于此,不优化。
