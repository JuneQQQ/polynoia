# ADR-011 — 右侧 slide-in Drawer 而非弹层 / inline 展开

- **状态**:accepted
- **日期**:2026-05-29
- **相关**:`apps/web/src/components/RightDrawer.tsx` + `drawer/AgentDetailView.tsx` + `drawer/MembersListView.tsx`

## 背景

P1.2 后用户反馈群聊页"很多按钮死的"。要求"按现代 IM 软件标准" — 点头像看人详情、点 "5 成员" 看成员列表、点搜索能搜。

候选交互方案:

| 方案 | 例 | 优 | 劣 |
|---|---|---|---|
| A · 浮层 popover | Discord 点头像 | 轻、不占空间 | 内容多了塞不下,要分多层导航 |
| B · inline 展开 | 飞书部分场景 | 不切走视线 | 撑乱布局,只能很短 |
| C · 模态 modal | 我们已有 ConvRolesModal | 内容多能塞 | 完全挡住聊天,情境断 |
| D · 右侧 slide-in drawer | Slack profile / Linear info panel | 不挡聊天主流,可塞长内容 | 桌面狭窄屏幕略挤 |

## 决策

**采用方案 D · 右侧 slide-in drawer**(`RightDrawer.tsx`),且做成 **router 模式** — 单个容器内部按 `store.rightDrawer.kind` 切两种 view(`agent-detail` / `members`)。点成员列表里的某行 → 同一 drawer 内切换到该 agent detail,带返回箭头。

宽度 420px,半透明 backdrop,Esc 关。开 drawer 时**自动关 PreviewPane**(都在右侧,互斥)。

## 为什么

- **情境保留** — 用户审 agent 详情时仍能瞥见聊天流(不像 modal 完全阻断)
- **可塞长内容** — Persona 上千字、Recent activity 列表、Action bar 三件套 都装得下
- **单容器多 view** — 减少新组件数,跟 PendingEditsPanel 同模式(都在 store + 一个组件)
- **跟 PreviewPane 视觉一致** — 同样右侧 slide,用户已熟悉这种结构
- **现代 IM 共识** — Slack / Linear / Notion 三家都用这模式,用户没学习成本

## 否则会怎样

- 浮层(A):Recent activity 5 条 + Persona 折叠装不下,搞复杂多层
- inline 展开(B):撑歪聊天流,用户讨厌
- modal(C):用户中断聊天,体感重

## 代价

- 跟 PreviewPane 互斥 — 同一时刻只能开一边。理论上用户想"边看人详情边看 web 预览"做不到。**接受** — 这种用法极罕见,且可以切回 PreviewPane 后用 ⌘K 搜索回想到的关键词

## 互斥实装

```typescript
openAgentDetail: (agentId) =>
  set((s) => ({
    rightDrawer: { kind: "agent-detail", agentId },
    preview: { ...s.preview, open: false },  // ← 自动关 PreviewPane
  })),
openPreview: (tab, data) =>
  set((s) => ({
    rightDrawer: { kind: null },  // ← 自动关 drawer
    preview: { open: true, tab, data: { ... } },
  })),
```

## 跟 PendingEditsPanel / AskFormsPanel 区别

那两个是**底部浮层**(composer 上方),用于**触发式**交互(等审批 / 等问答)。Drawer 是**右侧浮层**,用于**主动信息查阅**。互不冲突,可同时显示。
