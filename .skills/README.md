# Polynoia 自定义 Skill

每个 skill 是一份"标准化高频流程"的文档,目标是让团队成员 / AI 协作 follow 同一套流程,避免每次重新发明。

## 目录

| Skill | 状态 | 用途 |
|---|---|---|
| [`add-adapter`](add-adapter/SKILL.md) | ready | 接入一个新 backend(CLI 工具)成 Adapter |
| [`add-card-type`](add-card-type/SKILL.md) | ready | 新加一种 MessagePart 渲染卡 |
| `add-server` | P1+ | 新加 server kind(remote / tunnel)— 待写 |

## 如何写一个 skill

参考 `superpowers/5.1.0/skills/writing-skills`。简短 spec:

- `SKILL.md` 顶部 YAML frontmatter:`name: kebab-case-slug`, `description: 一句话用途`
- "何时用 / 不该用" 双向引导
- 步骤分 1-N,每步:文件路径 + 代码片段 + 测试 / 验证命令
- "关键陷阱" 节记录踩坑

## 与 superpowers skill 的关系

`.skills/` 是 **Polynoia 项目级** skill;`~/.claude/plugins/cache/superpowers-dev/` 是 **全局** skill。两者不冲突,Claude 在工作时都会读。
