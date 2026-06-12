# 提交历史页重设计 — 从 git 浏览器到「团队工作时间线」

> 2026-06-12 · 基于真实多 agent 历史(分支+合并+三作者)的实测诊断。
> 现状代码:`apps/web/src/components/preview/CommitHistoryView.tsx`(842 行)。

## 0. 立场

这个页面今天是一个**合格的 GitHub 式 commit 浏览器**(master-detail、日期分组、树模式、
split/unified、懒 LCS + content-visibility 性能功夫都在)。但 Polynoia 的提交不是普通提交——
**每一笔都是某个 agent 在某次对话里替用户干的活**。页面没有讲这个故事:它把 Polynoia
最独特的数据(提交 ↔ agent ↔ 对话 的三元关系)压缩成了一行 mono 小字。

优化方向:**少一点 git,多一点"谁为你做了什么"。**

## 1. 实测问题清单

| # | 问题 | 证据 |
|---|------|------|
| 1 | **布局挤压**:提交历史 tab 与右侧产物面板同开,diff 列仅 ~190px,split 模式完全不可读 | 截图实证;打开入口恰恰在产物面板里,必然同开 |
| 2 | **统计噪音且自相矛盾**:subject 内嵌机器统计 `(+24/-6)` 与右侧真实 stat chips `+5 −1` 并排出现、数字不一致 | agent 提交 message 由工具生成,含提交时点统计 |
| 3 | **main 徽章恒为 main**:列表模式所有行都标 main(合并后 reachable),纯噪音 | API `lane` 字段实测全为 main |
| 4 | **日期组重复**:同一天出现两个日期头(分组是顺序 run,不合并同 key;init 提交日期为当天但排最后) | 截图:6月12日 出现两次 |
| 5 | **列表/树模式心智不一致**:列表隐藏 merge 提交,树显示;而合并恰是多 agent 协作的关键事件 | `include_all=graph` 行为 |
| 6 | **已删除联系人显示裸 ULID**:26 字符 mono 撑爆行宽 | 实测(testkit 重置后旧 id 的提交) |
| 7 | **缺 Polynoia 独有能力**:无 agent 过滤;无"来自哪次对话"回链;无 copy-sha;无「回到这里」(后端 `restore-preview`/`restore` 端点已存在!);工作区改动行不可操作 | 代码审阅 |
| 8 | **固定 80 条无翻页**;**树模式 lane 颜色按序号轮换**,与 agent 颜色无关,读不出"谁的线" | 代码审阅 |

## 2. 设计

### 2.1 信息架构:三层叙事

```
┌─ 提交历史 ──────────────────────────────────────────────────────────────┐
│ 🔍 搜索提交/文件      [全部] 🟠阿核 🔵数擎 🟢制图 🟣文澜      已载 80 · 更多 │  ← A. 过滤条
├───────────────────────────────┬────────────────────────────────────────┤
│ ✎ 工作区改动(未提交)③        │  复核销售口径               +5 −1 · 1 文件 │
│ ── 6月12日 ─────────────────  │  🟣文澜 · 12小时前 · 1cb88c8 ⧉           │
│ 🟣 复核销售口径      +5 −1    │  〔在对话中查看〕〔回到这里〕              │  ← C. 详情头
│    文澜 · 12小时前            │ ┌──────────────────────────────────────┐ │
│ ▸ 🟢 制图 的交付     2 提交    │ │ ● sales-analysis.md       +5 −1  全文│ │
│    dash.css +38 · dashboard…  │ │   3 │− GMV: 1,204,332 元            │ │
│ ── 6月11日 ─────────────────  │ │   3 │+ GMV: 1,204,332 元(环比+12.4%)│ │
│ 🔵 生成销售分析      +88 −2   │ │  ……                                 │ │
│ 🔵 生成订单样例      +301     │ └──────────────────────────────────────┘ │
└───────────────────────────────┴────────────────────────────────────────┘
          ↑ B. 时间线(回合折叠)
```

**A. 过滤条(新)** — 顶部一排 agent 头像 chips(取自工作区成员 + 历史作者),点选过滤
该 agent 的提交;输入框按 message/文件路径过滤(前端先做已载集合内过滤,数据量大再下推
`git log --grep/-- <path>`)。右端是「已载 N · 加载更多」(`skip` 翻页,后端已支持)。

**B. 时间线(回合折叠,核心改动)** — 用户在乎"制图交付了看板",不在乎中间 3 个 commit。
把「同一 agent 在分支上的连续提交 + 收尾 merge」折叠为一张**回合卡**:

```
▸ 🟢 制图 的交付 · 2 提交 · 2 文件 · +158        ← 收起:一行摘要
▾ 🟢 制图 的交付                                  ← 展开:子行
  ├ edit dash.css        +38   16141b9
  ├ edit dashboard.html  +120  217ba97
  └ ⤵ 合并进 main        11:00
```

数据已够:flat 列表按 `parents` + 作者连续性聚类即可,无需后端改动。直接提交在 main 上的
单笔(用户编辑/快进合并)保持单行。点回合卡 = 选中其 merge(看整体 diff);点子行 = 单笔。
**列表与树从"两种模式"变成"同一数据的两个密度"**:回合卡展开后内嵌迷你 lane 线,树模式
保留给 power user,且 **lane 颜色 = agent 颜色**(main 线恒绿),多线并行时一眼认人。

**C. 行的解剖(去噪)**
- 第一行:**人话 subject**(`prettySubject` 之外,再剥掉尾部 `(+N/-M)` 机器统计)+ 右对齐真实 stat chips。
- 第二行:agent 名片(彩点+名,**已删除联系人降级为灰名片「已移除 · 1CB8」**)· 相对时间。
- sha 移到 hover 才显示的 `⧉ 1cb88c8`(点击复制);列表模式**删掉 main 徽章**(树模式保留分支/main 区分)。
- 日期分组改为 Map 合并同 key(修重复 bug);merge 在列表模式渲染为细分隔行「⤵ 合并 · 你 · 11:00」而非隐藏。

### 2.2 Provenance 回链(Polynoia 独有)

分支名/merge message 携带 `ag-<agentId>-conv-<convId>`。解析出 convId 后,详情头给
「在对话中查看」按钮 → `polynoia:select-conv` 事件(已有,App 监听)直达产生这笔提交的会话。
反向(对话 → 提交)已有 diff 卡,这是补全闭环的另一半。解析不出 conv 的提交(用户编辑/init)不显示按钮。

### 2.3 操作闭环

- **「回到这里」**:挂后端已有的 `restore-preview`(预览将撤销哪些提交)→ `ConfirmDialog`
  列出受影响提交和文件数 → `restore`。入口只放在详情头,危险色。
- **工作区改动行**:加「丢弃全部」(ConfirmDialog,git checkout --)— 收集为提交暂不做
  (capture 已有自动机制,避免双轨)。
- **copy sha**、文件 diff 头加「在文件树中打开」。

### 2.4 布局修复

- 打开提交历史 tab 时**自动收起右侧产物面板**(入口就在面板里,留着必挤压);关 tab 不自动恢复。
- diff 列宽 < 720px 时强制 unified(忽略 split 偏好,带提示);恢复宽度后还原。

## 3. 实施切片

| 片 | 内容 | 估量 |
|----|------|------|
| **P0 修复** | 挤压(自动收面板+窄屏强制 unified)· subject 去统计后缀 · 删列表 main 徽章 · 日期组合并 · 删除联系人灰名片 · copy sha · 加载更多 | 半天 |
| **P1 叙事** | agent 过滤条 · 回合折叠卡 · 列表显示 merge 分隔行 · 树模式 agent 配色 | 1 天 |
| **P2 闭环** | 「在对话中查看」回链 · 「回到这里」(restore-preview→ConfirmDialog→restore) · 工作区改动丢弃 | 1 天 |

每片独立可交付,P0 不动数据结构,P1/P2 也零后端 schema 改动(全部复用现有端点)。

## 4. 实施状态(2026-06-12 全部完成)

P0/P1/P2 一次性落地并实测通过(cua-driver/Playwright 驱动真实多 agent 历史)。

- **新文件**:`apps/web/src/lib/commitStory.ts`(纯逻辑:`stripStatSuffix` / `parseConvFromText` / `parseAgentFromText` / `firstParentChain` / `buildTimeline` 回合折叠 / `groupByDay` 合并)+ `commitStory.test.ts`(9 测试全绿)。
- **重写**:`CommitHistoryView.tsx` —— 回合卡(单作者分支+merge 折叠,可展开)、agent 头像过滤条 + 搜索、merge 细分隔行、删除联系人灰名片、copy-sha、加载更多、详情头「在对话中查看」回链 + 「回到这里」(restore-preview→ConfirmDialog→restore)、工作区改动「丢弃」、窄列(<720px)强制 unified、graph lane 色=agent 色(main 恒绿)。
- **后端**:`POST /api/workspaces/{id}/discard-working`(`Sandbox.discard_working_changes`,merge-lock 守护、拒绝半合并、保 ignored/.polynoia/worktree)+ `test_discard_working.py`(3 测试)。`api.ts: workspaceDiscardWorking`;`store.openCommitsTab` 自动收产物面板。
- **provenance 依赖**:回链要求 merge 提交消息含 `agent/<id>/conv-<id>`(沙箱合并已是此格式)。旧式无标记的提交不显示「在对话中查看」(优雅降级)。
- **门禁**:前端 tsc 干净 / vitest 182 passed(+9 commitStory;仅 2 个既有 M3 CJK 红)/ build 绿 / biome 我的文件 0 lint;后端 513 passed(+3 discard;1 个既有 keychain 环境红)。所有改动未 commit。
