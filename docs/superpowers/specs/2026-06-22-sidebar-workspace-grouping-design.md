# 侧栏「工作区 → 会话」分级 — 设计

> 日期:2026-06-22 · 分支:`feature/fzf_dev` · 类型:前端 IA(信息架构)
> 关联:`Sidebar.tsx`(Layer 1)、`NewConvModal`、已有 Layer 2 工作区视图

## 1. 问题

一个工作区(Workspace)天然「一对多」会话——`Conversation.workspace_id`(可空)指向
`Workspace`,后端已支持。但侧栏 Layer 1(全局视图)把**所有**会话(单聊 / 群聊 /
项目会话)铺成一个**扁平列表**,只在每行末尾留一个工作区色小点(hover 才看得到名字)。
结果:用户**看不出**「会话A 和 会话B 属于同一个工作区」,「一个工作区多个会话」的结构
完全不可见。

此外,展开态的 Layer 1 **没有任何入口**能进入工作区(已存在的 Layer 2「工作区详情」
视图只能在折叠成窄条 rail 时由底部方块图标进入,或刚建完工作区时自动进入)。

## 2. 目标

把 Layer 1 从「平铺」改成「按工作区分组的可折叠树」:

- 每个工作区是一个**可折叠分组**(色点 + 名字 + 会话数 + 进详情图标 + `+` 新建会话)。
- 工作区下嵌套该工作区的会话。
- `workspace_id == null` 的单聊 / 群聊统一收进底部的**「直接消息」分组**。
- 两条创建路径:① 顶部「新建对话」→ 弹窗内选工作区(已有);② 工作区标题行 `+` → 直接
  在该工作区建会话(新增,预绑定)。

非目标:不碰后端;不碰折叠窄条 rail(已按工作区图标分组);不碰冲突闭环 CHARTER 的
承重符号(`api/routes.py` 合并 / burst 区、`sandbox` git helper、pending-edit、
PreviewPane、PARTS_REGISTRY、store merge 轨道)。

## 3. 数据流(纯前端,零后端改动)

分组是**对已加载数据的纯客户端变换**。`Sidebar` 已有:

- `allConvs: ConversationSummary[]` — 所有未归档会话(已含 `workspace_id`、`pinned`、
  `last_message_at`、`created_at`)。
- `workspaces: Workspace[]` — store 里的全局工作区列表。

按 `conv.workspace_id` 分桶即可,不需要新接口、不需要按工作区分别请求(避免 N+1)。

## 4. 组件拆分(顺手给 1817 行的 `Sidebar.tsx` 减负)

新建 `apps/web/src/components/sidebar/`(对齐已有 `preview/` 子目录约定):

| 文件 | 职责 |
|---|---|
| `groupConversations.ts` | **纯函数**:输入 `allConvs / workspaces / query` → 输出有序分组。承载全部分桶 / 排序 / 过滤逻辑。无 React、可单测。 |
| `ConvListRow.tsx` | 抽出当前内联在 Layer 1 的「全功能会话行」(头像簇 / 草稿 / 未读 / pin / `ConvActionsMenu`)。组内复用。 |
| `WorkspaceGroupHeader.tsx` | 工作区分组标题行(见 §5)。 |
| `SidebarConvGroups.tsx` | 调 `groupConversations`,渲染分组树 + 折叠态(localStorage)+ 空状态。**替换** Layer 1 当前那段平铺列表。 |

`Sidebar.tsx` 仅:① 用 `<SidebarConvGroups …/>` 替换内联平铺块(约 1101–1291 行);
② 新增一个 `newConvWorkspace` 状态 + 对应的 `NewConvModal`(workspace 预绑定);
③ 把 handler 透传下去。顶部那段「服务端搜索额外命中」块(消息体匹配)保持不动。

## 5. 工作区标题行 & 交互

```
[▾/▸] [● 色点] 测试共享区的工作区   (2)   [⊞ 进详情]   [ + ]
   └ 点 chevron / 名字 / 色点 = 就地展开/收起(纯树形交互)
                                 └ 进 Layer 2(setActiveWorkspace) └ 新建会话(预绑定本工作区)
```

- **进详情图标** → `setActiveWorkspace(ws.id)`,复用现有 Layer 2(返回箭头 + 设置
  `SlidersHorizontal` + 文件入口已在那)。**Layer 2 保留**作为深入页;工作区的
  设置 / 删除 / 重置沙箱仍在 Layer 2,标题行保持精简。
- **`+`** → `setNewConvWorkspace(ws)`,打开 `NewConvModal workspace={ws}`(已支持预绑定,
  成员自动限定该工作区成员)。
- **「直接消息」分组标题** 更简单:`[▾/▸] 直接消息 (N)` + 一个 `+`(打开
  `NewConvModal workspace={null}`,与顶部「新建对话」等价,就近)。

## 6. 排序 / 默认态 / 搜索 / 空状态

- **组排序**:工作区组按「组内最近活跃时间」(`max(last_message_at)`,无则 `created_at`,
  再无则 0)倒序;空工作区(0 会话)recency=0 自然沉底,同分按名字升序。「直接消息」组
  **恒定置底**。
- **组内排序**:`pinned` 置顶 → recency 倒序(显式比较器,不依赖后端返回顺序)。
- **默认展开**:所有组默认展开;折叠状态按组 id 存 `localStorage`
  键 `polynoia:sidebar-collapsed-groups`(JSON `string[]`;工作区用 `ws.id`,直接消息用
  哨兵 `"__dm__"`)。`useState` 初始化时同步读取(`renderToStaticMarkup` 下也成立,
  不依赖 effect)。切到某会话时,把它所在的组从折叠集合移除(自动展开)。
- **搜索**(输入 `q`):组内按标题过滤,隐藏全空组(含空工作区);有命中的组强制展开
  (覆盖折叠态)。整库搜索(消息体)仍走顶部已有的服务端搜索块 + Cmd+K 浮层,不变。
- **空状态**:
  - 整个 app **一个工作区都没有**时 → 退回今天的纯平铺(不渲染任何分组标题)。
  - 展开的**空工作区**组 → 组内显示一句引导 + `+`(复用 Layer 2 空状态文案
    `emptyWorkspaceHint` / `createFirstConversation`)。
  - 首次加载未完成(`!convsLoaded`)→ 保留 `ConvListSkeleton`。
- **孤儿会话**(`workspace_id` 有值但 `workspaces` 里找不到,极少见的刷新竞态)→ 落入
  「直接消息」组兜底,保证不消失;工作区列表刷新后自愈。

## 7. 边界 & i18n

- **折叠窄条 rail**:已按工作区方块图标分组,**不动**。
- **移动端**:同一组件,自动得到分组(侧栏即移动端首页列表)。
- **i18n**(zh + en 同时给):
  - `directMessages`:直接消息 / Direct messages
  - `newConvInWorkspace`(`+` 的 title/aria):在此工作区新建会话 / New conversation here
  - `openWorkspace`(进详情图标 title/aria):打开工作区 / Open workspace
  - 复用:`newConversation` / `directMessageType` / `groupChatCountLabel` /
    `emptyWorkspaceHint` / `createFirstConversation` / `workspaceTooltip`。

## 8. 测试

- `groupConversations.test.ts`(纯函数,jsdom-free):分桶正确;`workspace_id=null` → 直接
  消息组;直接消息组恒置底;组按 recency 排序、空工作区沉底;组内 pinned 置顶;搜索过滤
  隐藏全空组;孤儿会话兜底进直接消息组;无工作区时只返回直接消息组。
- `SidebarConvGroups.test.tsx`(`renderToStaticMarkup` + `vi.mock` store/api,沿用
  `mobile.viewport.test.tsx` 约定):渲染出工作区组标题 + 计数 + 会话标题 + 「直接消息」
  标题;无工作区时不渲染分组标题(平铺回退)。

## 9. 验收(做完什么算成功)

1. Layer 1 展开态下,同一工作区的多个会话**可见地**聚在一个带名字 / 色点 / 计数的可折叠
   组里;`workspace_id=null` 的会话在底部「直接消息」组。
2. 点工作区标题行就地展开 / 收起;折叠态刷新后保留。
3. 工作区标题行 `+` 能在**该**工作区直接建会话(NewConvModal 预选该工作区)。
4. 进详情图标进入 Layer 2;顶部「新建对话」与「直接消息」组 `+` 走 workspace=null 路径。
5. EN 模式无漏译;`make lint` / `make types` 不破;新增测试通过。
