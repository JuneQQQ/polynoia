# Polynoia(AgentHub)文档索引

> 新读者从这里进。仓库根 `CLAUDE.md` 是 AI 协作规范 + 当前进度;本文是 `docs/` 的总目录。

## 入门 / 协作规范
- [`/CLAUDE.md`](../CLAUDE.md) — 项目级 AI 协作规则 + 技术栈 + 当前进度(§10)。
- [`ai-collaboration.md`](ai-collaboration.md) — 官方提交用的「AI 协作能力」说明(Spec/Rules/Skills/ADR/测试/分工/自主质量闭环)。
- `rule.md`(仓库根,gitignored)— 官方赛题与验收映射(本地文件,不入库)。

## Spec & 调研
- [`superpowers/specs/2026-05-23-polynoia-design.md`](superpowers/specs/2026-05-23-polynoia-design.md) — 完整产品 + 系统设计 Spec。
- [`research/00-SYNTHESIS.md`](research/00-SYNTHESIS.md) — 20 库深度调研综合(+ `research/01..06`、UI 设计、agent-memory)。

## 架构与设计
- [`ADR/README.md`](ADR/README.md) — 23 篇架构决策记录索引(为何选 X 不选 Y)。
- [`design/`](design/README.md) — 系统/产品/工具设计文档(上下文系统、工作区共享 git、冲突闭环 CHARTER、预览系统、diff-sandbox-mcp、中文文案审计、右栏路线图)。

## 图示
- [`diagrams/README.md`](diagrams/README.md) — 12 张结构化图示(协议映射、运行时、数据 schema、三端等)的索引。

## 测试
- [`testing/manual-test-cases.md`](testing/manual-test-cases.md) — 手动测试矩阵(UI/工作流验收基线)。
- 自动化:后端 `apps/server/tests/`(pytest)、前端 vitest;`scripts/testkit/`(20 案 seed + `check_case.py` 不变量)。

## 会话纪要(过程留痕,非永久参考)
- [`sessions/`](sessions/) — 自主开发/测试会话记录:夜间进展、通宵 20 案 E2E、ping-pong/进程生命周期修复等。这些是「AI 自主跑回归→诊断→修→复验」的可审计痕迹,不是设计规范。
