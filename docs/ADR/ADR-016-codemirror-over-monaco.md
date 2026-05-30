# ADR-016 — 工作区编辑器:增强 CodeMirror 6,不引 Monaco

- **状态**:accepted
- **日期**:2026-05-30
- **相关**:`apps/web/src/components/preview/CodeTab.tsx`、`DiffReviewPane.tsx`、ADR-010(workspace file API)、CLAUDE.md §3(技术栈锁定,原列 ❌ Monaco)

## 背景

工作区代码面板(`CodeTab.tsx`)目前是**自建编辑器**:手写文件树 + `@uiw/react-codemirror`(CodeMirror 6)+ 多 tab + `Ctrl+S → PUT /api/workspaces/{id}/files → 自动 commit`,配套 `DiffReviewPane`(`@git-diff-view/react`)做 Cursor/Windsurf 式绿/红逐 hunk Accept/Reject。

用户提出:**"能不能直接嵌入现成的库,给用户 VSCode 那种体验?"** —— 实质是要求**重新审视 CLAUDE.md §3 里 `❌ Monaco Editor(包过大)` 这条**。

为此做了一轮多 agent 调研(4 路并行评估 + 综合),覆盖:`@monaco-editor/react`、增强 CM6、`@codesandbox/sandpack-react`、`monaco-vscode-api / vscode-web`,以及我们当前实现的差距。4 路结论一致。

## 决策

**保留 CodeMirror 6 作为工作区编辑器,增量增强;P0 不切 Monaco。**
把 CLAUDE.md §3 的 Monaco 由"硬排除"改写为**"P1+ 推迟,触发条件明确"**(指向本 ADR)。

### 本轮已落地的增强(`CodeTab.tsx`)

1. **VSCode 键位**:`@replit/codemirror-vscode-keymap`(`Prec.high`,优先于 CM 默认键位)
2. **小地图 Minimap**:`@replit/codemirror-minimap`(工具栏可开关,`localStorage` 持久化,默认开)
3. **查找/替换**:`@uiw` 的 `basicSetup` 本就内置(`Ctrl+F` 一直可用),新增工具栏按钮 `openSearchPanel` 显式暴露
4. **Ctrl+S 进 keymap**:`Prec.highest` 的 `Mod-s` 取代旧 DOM `keydown` 监听(顺手删掉空挂的 `editorRef`)
5. **文件树列**:两态折叠(完全隐藏,非最小图标栏)+ 拖拽改列宽(140–480px,持久化)—— 用户单独提的需求

### 新增依赖(均 CM6 生态,gzip 合计 ~10–15KB)

`@codemirror/search`、`@codemirror/commands`、`@codemirror/autocomplete`、`@replit/codemirror-vscode-keymap`、`@replit/codemirror-minimap`

## 为什么

| 维度 | CM6 增强 | Monaco / monaco-vscode-api |
|---|---|---|
| 体积(gzip) | CM6 核心 ~250KB,本轮 +10–15KB | `@monaco-editor/react` ~1.5–2.5MB;monaco-vscode-api ~3–5MB |
| "VSCode 感" | 语法/折叠/多光标/查找替换/键位/minimap ≈ 75–80% | 100%(含 LSP/IntelliSense/命令面板) |
| 我们的胶水 | 文件树 / `Ctrl+S→PUT→commit` / DiffReviewPane **全部保留** | **零支持** —— 树、保存、commit、diff 评审都要重写 |
| 懒加载 | 不需要(本就够小) | 只能推迟首次拉取,2MB+ 下载/解析成本一打开 Code tab 就付 |
| 工作量 | ~6–8h | 基础切换 5–8h,接回 diff 评审后膨胀到 8–12h+;monaco-vscode-api 2–3 周 |

- **"包过大"在 2026 年仍成立**:Monaco 比 CM6+git-diff-view 重 3–10×,而它多出来的 20%(真 LSP/命令面板)**不是**让 Polynoia 面板"像 VSCode"的关键——用户编辑的是 agent 改过的**小片段**,在 编辑→评审→批准 闭环里,不是 10 万行大仓。
- **Monaco 对我们的核心场景零增益**:我们要的是 (1) 经 REST 读写工作区任意文件、(2) git 自动 commit、(3) 配对 diff 面板评审、(4) manual-mode 待批编辑——Monaco 是单体编辑器,以上四点都得自己接;CM6 本就是"自己接保存/合并逻辑"的基座层。
- **当前差距是 UX 抛光,不是架构问题**:minimap / 查找按钮 / 折叠树 都是几十行增量。

## 否则会怎样

- **切 Monaco**:为零收益付 2MB+ gzip + worker 注册复杂度;rip-and-replace 打断 DiffReviewPane 集成;3–4 周重写 + 集成测试;锁进 Monaco 的架构选择(树是内建的,文件浏览器没法和编辑面板分开)。
- **上 Sandpack**:它面向 npm 沙箱/bundler 预览,不是"经 REST 编辑真实工作区文件",场景错配。
- **什么都不做**:用户感知编辑器"很简陋"(其实查找一直在,只是没按钮),"VSCode 体验"诉求落空。

## 代价

- 5 个新依赖(都是 CM6 生态小包),`pnpm-lock.yaml` 增量
- `@replit/*` 两个第三方包引入维护面(广泛使用,风险低)
- minimap 是 canvas 叠层,运行时渲染未在本次无人值守会话里肉眼验证(已过 tsc + vite build;放在开关后,出问题一键关)
- CLAUDE.md §3 措辞需同步(本 ADR 配套改)

## 何时反悔(切 Monaco 的触发条件)

- **真 LSP / IntelliSense** 成为明确产品需求(跨文件跳转、类型诊断、重构)
- **命令面板**(VSCode `Cmd+Shift+P` 式)成为核心交互
- 届时按 **power-user opt-in、懒加载**接入 `monaco-vscode-api`,**不设为默认**;若 VSCode 把 diff editor 作为一等出口导出,亦可重估
