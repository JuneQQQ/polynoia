# 设计文档索引

> 系统/产品/工具的设计说明。架构**决策**(选 X 不选 Y)在 [`../ADR/`](../ADR/README.md);本目录是更长的设计阐述。

## 系统设计
- [`context-system.md`](context-system.md) — 自管理五层上下文(identity/briefs/ledger/history/window)+ 预算与截断。
- [`workspace-shared-git.md`](workspace-shared-git.md) — 工作区共享 git + 每 agent worktree + 合并模型。
- [`conflict-closed-loop-CHARTER.md`](conflict-closed-loop-CHARTER.md) — ⚠️ **承重区契约**:改 `api/routes.py` 合并/burst、`sandbox/_core.py`、pending-edit、前端 store/PreviewPane/PARTS_REGISTRY **之前必读**。
- [`conflict-closed-loop-2026-05-30.md`](conflict-closed-loop-2026-05-30.md) — 冲突闭环完整设计(检测→开卡→resolve→合并→不变量)。

## 产品 / UI 设计
- [`preview-system-and-evolution.md`](preview-system-and-evolution.md) — 产物预览栈(Code/Diff/Web/Office/Markdown)与演进。
- [`chinese-editorial-audit.md`](chinese-editorial-audit.md) — 中文文案审计(对外措辞一致性)。

## 基础设施 / 工具
- [`diff-sandbox-mcp-2026-05-27.md`](diff-sandbox-mcp-2026-05-27.md) — diff / 沙箱 / MCP 工具链设计。

## 历史 / 已回滚(保留作决策留痕)
- [`right-rail-roadmap.md`](right-rail-roadmap.md) — ⚠️ **历史**:含 2026-06-01 Docker project runner 回滚的决策记录(因延迟/复杂度/需求不匹配,改用终端面板)。非当前活跃路线图。
