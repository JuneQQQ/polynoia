# ADR-010 — Workspace 文件 API 的路径安全 + 直读策略

- **状态**:accepted
- **日期**:2026-05-28
- **相关**:`apps/server/polynoia/api/routes.py`(`_resolve_safe_path`,`list_workspace_files`,`read/write_workspace_file`,`preview_workspace_html`)

## 背景

P1.2 加了两个 user-facing 功能:网页 iframe 真预览 + 可写代码编辑器。两者都需要从 workspace-shared sandbox(`~/sandbox/polynoia/workspaces/<ws_id>/`)读写文件。

候选方案:
1. **agent MCP 新工具** — 加个 `create_preview(html)` / `save_file(path, content)` MCP 工具,agent 显式调
2. **直接 HTTP 读写** — UI 用 HTTP CRUD workspace 文件,跟 agent 解耦

## 决策

**HTTP 直读 + 路径 traversal 强校验**。

四个新 endpoint:
- `GET /api/workspaces/{ws_id}/files?path=` — 列一层目录
- `GET /api/workspaces/{ws_id}/files/raw?path=` — 读 UTF-8 文本(>1MB 拒)
- `PUT /api/workspaces/{ws_id}/files/raw?path=` — 写 + auto-commit main
- `GET /api/workspaces/{ws_id}/preview?file=` — 服 text/html + CSP sandbox

共享 helper `_resolve_safe_path(workspace_root, rel_path)`:
- 拒绝绝对路径
- 拒绝 `..` 逃逸(`Path.resolve().relative_to(root)` 验)
- 跟随 symlink — 必须仍在 workspace 内

列表 endpoint 跳过 `.git / .polynoia / worktrees / node_modules / *_cache / 隐藏文件`。

## 为什么 HTTP 直读 而非 agent MCP 工具

- **agent 不需要参与** — preview 是 user 想看哪个就看哪个,跟 agent 当前 turn 解耦
- **代码编辑器是 user 控制权** — 用户改文件 + 提交,不该等 agent 同意
- **零新 agent 工具** — agent 已经能写 HTML 文件了(`mcp__polynoia__write`),UI 直接读它的输出即可
- **统一存储模型** — 不引入"preview record"这种新概念,workspace 文件**就是** preview 源

## 路径安全为什么这样设计

`Path.resolve().relative_to(root)` 是 Python stdlib 提供的最稳健的 traversal 防护:
- `resolve()` 把 `..` / `.` 全部 normalize,跟随 symlink
- `.relative_to(root)` 是字符串前缀检查,若 resolved 不在 root 下抛 ValueError
- 我们抓 ValueError 转 400

试了的 attacks:
- `?path=../../etc/passwd` → resolved 不在 workspace 下 → 400
- `?path=/etc/passwd` → is_absolute() → 400
- `?path=a/../../etc` → resolved 出 root → 400
- `?path=a/../b`(b 在 root 内)→ 允许,语义符合预期

## 预览 endpoint 为什么 sandbox CSP

agent 生成的 HTML 可能含恶意脚本(用户故意让 agent 写 `<script>fetch(...)</script>` 探查内网等)。`Content-Security-Policy: sandbox allow-scripts allow-same-origin` 让 iframe:
- 不能 navigate top frame(防钓鱼)
- 不能弹窗
- script 可以跑(P0 用户可能就是要测试 JS)
- 同源(可以 fetch 其它 workspace 资源,符合预期)

`allow-same-origin` 是个 trade-off — 允许 iframe 内的 fetch 触达 Polynoia 自身。但是因为 iframe 跑在我们自己的 origin 内,无 cookie 也无 auth,API 调用就跟未登录用户一样,风险可控。

## 否则会怎样

走 MCP 工具方案 → agent 多一组 API 表面,文档要写,prompt engineering 复杂度 +1;且 user-driven 操作要 agent 配合,UX 差。

## 代价

- workspace 内任何**用户**都能改任何文件(P0 单机模式无 user auth)。生产 multi-tenant 部署时需要在 endpoint 前加 user/workspace 鉴权层
- list endpoint 跳过隐藏文件 / `.git` 等 —— 用户想看 `.env` 看不到。这是个故意的安全 trade-off
