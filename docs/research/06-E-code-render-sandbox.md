# Cluster E 研究:代码 / 沙盒渲染库深读

> 来源:subagent E 深度调研
> 库:Monaco Editor / Sandpack / WebContainer API / react-diff-view & @git-diff-view
> Clone:`/data/lsb/polynoia/research/E-coderender/` 已归档

---

## Monaco Editor

**版本:** `monaco-editor@0.55.1`(HEAD = `5f59e47ae69abd1fc99d57267486384dc44ea8ca`)。Repo 是薄 shim — 实际编辑器在 `vscode/src/vs/editor` at commit `86f5a62f058e3905f74a9fa65d04b2f3b533408e`(per `package.json#vscodeRef`)。VS Code sparse-checkout at `/data/lsb/polynoia/research/E-coderender/vscode-editor-src/`
**License:** MIT
**Bundle size:** Tarball **~72MB 解压 / 1734 文件**。实际用 `monaco-editor-webpack-plugin` 限到几个语言:**~1-2 MB JS + workers**。编辑器在 dedicated workers 跑 language servers(`editor.worker, ts.worker, json.worker, css.worker, html.worker`)

**是什么。** VS Code 同一编辑器,重打包成独立浏览器模块。两顶级产品:*代码编辑器*(单文件、语法高亮、IntelliSense、decorations、查找替换、多光标)和 *diff 编辑器*(两并排或 inline-overlay `CodeEditorWidget` 带计算 line changes)。

**心智模型。** 三概念主宰 API:
- **`ITextModel`** — 不可变字符串 buffer + 语言归属,经 URI 标识
- **`IStandaloneCodeEditor`** — 附 DOM 元素渲 model 的视图
- **`IModelDeltaDecoration`** — range + options for inline 视觉(波浪线、glyph margin、line-class)

编辑器用 *standalone services* fork(`vs/editor/standalone/browser/standaloneServices.ts`)— 削减的 VS Code DI container,不拽 workbench 代码。Decorations 经 `editor.deltaDecorations(oldIds, newDeltas)` 协调(`src/vs/editor/common/model.ts:444`)。**Diff Editor 是 `class StandaloneDiffEditor2 extends DiffEditorWidget`**(`standalone/browser/standaloneCodeEditor.ts:498`),`DiffEditorWidget` 包两个 `ICodeEditor` 经 `getOriginalEditor() / getModifiedEditor()`(`diffEditor/diffEditorWidget.ts:540-541`)暴露,diff 计算在 worker 内 surface 经 `onDidUpdateDiff: Event<void>`(line 548)。

**集成 API。** **命令式** — 你自己 mount:
```ts
// vscode-editor-src/src/vs/editor/standalone/browser/standaloneEditor.ts:49
export function create(domElement: HTMLElement, options?: IStandaloneEditorConstructionOptions, override?: IEditorOverrideServices): IStandaloneCodeEditor
// :98
export function createDiffEditor(domElement: HTMLElement, options?: IStandaloneDiffEditorConstructionOptions, override?: IEditorOverrideServices): IStandaloneDiffEditor
// :225
export function createModel(value: string, language?: string, uri?: URI): ITextModel
```

React 用社区库 `@monaco-editor/react@4.7.0` 提供 `<Editor />` 和 `<DiffEditor />`。**SSR caveat:** Monaco 在模块加载时访 `window` 和 `Worker` — 必须 dynamic-import on client only(`@monaco-editor/react` 经自己 `loader` 机制处理)。Vite 无 React wrapper 时需 `userWorker.ts`(MS 官方 sample)注入 `self.MonacoEnvironment.getWorker`。

**Polynoia 用关键功能:**
- **语言模式** 经 `monaco.languages.register(...)` 注册,Monarch grammars 或 LSP token 化 — Polynoia 代码 tab 可用内置(`typescript, javascript, css, html, json, markdown`)
- **Diff editor:** `monaco.editor.createDiffEditor(el, {renderSideBySide: true, readOnly: false, originalEditable: false})`。Feed 经 `editor.setModel({original, modified})`。**天然解 Polynoia diff message 渲,甚至给 free 三方编辑 if 你 flip `originalEditable`**
- **Decorations API** — `IModelDeltaDecoration` in `src/vs/editor/common/model.ts:373`。**Polynoia diff `hunks: [kind, lineNo, text]` 干净映射 inline decorations** 带 `options.className = 'hunk-insert'` 等
- **Theming:** `monaco.editor.defineTheme()`(`standaloneEditor.ts:407`)。Polynoia 匹配其调色板

**性能 / 包含义。** 无 tree-shaking:~3 MB gzipped。**带 `monaco-editor-webpack-plugin` 限到 `['typescript','javascript','css','json','markdown']`:~1-1.5 MB JS + ~500 KB workers**。Workers 隔离;每语言首次创 model 时按需载。**懒载:整模块包 `React.lazy()` 让 chat 窗口首次渲不被阻塞**。

**Polynoia 启示:**
- **直接用:** diff editor — 内置 render-side-by-side toggle, gutter changes, 拖 hunks 来 "stage" — **正是 diff message UX 映射**
- **加改造用:** code-tab 编辑器 — Polynoia 必须懒载并只用 `typescript|javascript|json|css|html|markdown` 保 chat 线程响应
- **避开:** 启 TS/JS/JSON/CSS/HTML 之外的 LSP/IntelliSense services — 每额外语言拉 ~200 KB worker
- **缺口暴露:** Polynoia diff 卡需要 *partial* hunk-level apply("仅应用 hunk 2,拒绝 1, 3")。**Monaco diff widget 不开箱暴露 hunk-pick UI**;需在 line-decoration gutter 渲 checkboxes 并用 `editor.getLineChanges()` 投到 patch

**判定:** ★ 直接用 — 同类编辑器和 diff UX 最佳,带懒载 + 包剪 caveat。

---

## `@codesandbox/sandpack-react`

**版本:** monorepo HEAD `7d60a4334980eef304d53b1c3df371ed6dbcf491`;`@codesandbox/sandpack-react@2.20.0`, `@codesandbox/sandpack-client@2.19.8`
**License:** Apache-2.0
**Bundle:** ~270 KB gzipped(不含 workers)+ CodeMirror 6 ~150 KB。实际 *bundler* 跑在 `https://*-sandpack.codesandbox.io`,**多数重活远程**

**是什么。** React 组件集包 CodeSandbox 的浏览器 bundler。两执行后端:
1. **`SandpackRuntime`** — 在 `*-sandpack.codesandbox.io` 远程 iframe-hosted bundler 跑(支持 React/Vue/Svelte/Solid/Angular/vanilla)
2. **`SandpackNode`** — 经 `@codesandbox/nodebox` 在浏览器**本地**跑 Node.js(Vite/Next.js/Astro templates),用单独 emulator iframe

**心智模型。** `SandpackProvider` 持 state(files map, status, errors);`SandpackClient`(子类 `SandpackRuntime` 或 `SandpackNode`)拥 iframe 并经 `postMessage` 调度消息,channel name from `runtime/types.ts:CHANNEL_NAME`。`Sandpack` preset 是组合:`<SandpackProvider><SandpackLayout><SandpackCodeEditor /><SandpackPreview /></SandpackLayout></SandpackProvider>`。**编辑器是 CodeMirror 6**(不是 Monaco)。

**集成 API。**
```tsx
<Sandpack
  template="vite-react-ts"
  files={{ "/App.tsx": "...", "/index.html": "..." }}
  options={{ showConsole: true, recompileDelay: 300 }}
  customSetup={{ dependencies: { react: "^18", "react-dom": "^18" } }}
/>
// 可组合:
<SandpackProvider template="react" files={files}>
  <SandpackLayout>
    <SandpackCodeEditor showLineNumbers showInlineErrors />
    <SandpackPreview showNavigator showOpenInCodeSandbox={false} />
  </SandpackLayout>
</SandpackProvider>
```

File API 经 hook:`const { code, readOnly, updateCode } = useActiveCode();` 和 `updateFile, addFile, deleteFile from useSandpack().sandpack`。SSR 工作因 Sandpack 推迟 iframe 创建到 effect。

**Polynoia 用关键功能:**
- **File map** 是普通 `{ "/App.tsx": { code: "..." } }` — agent 输出可轻量构造
- **Iframe sandbox 严锁**:`sandbox="allow-forms allow-modals allow-popups allow-presentation allow-same-origin allow-scripts allow-downloads allow-pointer-lock"`。**AI 生成代码的好安全默认**
- **Listener 模型:** `useSandpackClient().listen((msg: SandpackMessage) => ...)` — 捕 console 输出、错误、resize 事件
- **`<SandpackConsole>`** 捕 bundler console;**`<ErrorOverlay>`** 显运行 / 编译错误
- **Recompile:** `recompileMode: "delayed", recompileDelay: 300` 触发 rebundle when 编辑流入

**性能 / 包。** ~270 KB gz for sandpack-react + ~150 KB CodeMirror + 动态主题。**Bundler iframe 自身按需从 CodeSandbox CDN 加载传递依赖**。意味**每新依赖一网络往返** — 冷启复杂 React + Tailwind app 初始预览可 5-10 秒。`@codesandbox/sandpack-react/unstyled` 移除 stitches styles(~30 KB 省)。Bundler URL 指向 *CodeSandbox 基础设施* — **Polynoia 会依赖第三方 CDN 执行代码**。Sandpack 提供 `bundlerURL` 自托管选项,但维护非平凡。

**Polynoia 启示:**
- **直接用:** 预览 agent 来的小 React/Vue 片段适合内置 template 时,`<SandpackPreview />` 时间到像素无敌
- **加改造用:** 建薄 `<PolynoiaPreview kind="react|vue|html">` 包装 — 挑 template, 设 files, surface `listen` events 到 Polynoia 通知系统
- **避开:** 把 Sandpack 当 *编辑器* — 其 CodeMirror 有好语法高亮但**无 IntelliSense、无 diff editor、无 LSP**。**Polynoia 的 "code 二次编辑" tab 需要重编辑富 agent 代码带提示,优先 Monaco**
- **缺口暴露:** Polynoia 当前无 "agent 产了什么 artifact 形" 的正式合约(React app vs raw HTML vs Next.js page)。**Sandpack 的 `template` 字段强制这分类**。Polynoia 应加 `card.preview.template` 到协议

**判定:** ◐ 加自定义包装用 — artifact 是小 client-only React/Vue/static 片段时 `web` 预览完美;**全 Next.js/server-side apps 不对路**

---

## `@webcontainer/api`

**版本:** `@webcontainer/api@1.6.4`(tarball extracted to `webcontainer-api-npm/`)。**运行时本身闭源** — GitHub repo `stackblitz/webcontainer-core` 只 ship 浏览器怪癖 markdown(`browsers/brave.md`)。Quickstart at `webcontainer-api-starter@HEAD`(Vite-based)
**License:** MIT(npm shim)
**Bundle:** 页面侧极小(`@webcontainer/api` 自身 ~30 KB gzipped)。*运行时* 从 `webcontainer-api.io` 载进 sandboxed iframe — 那是实际 Node.js-in-WASM payload(~3-5 MB),激进缓存

**是什么。** StackBlitz 的浏览器内 Node.js 运行时,跑*真正* Node — `npm install`、dev servers、文件 watchers,所有 — 通过把 Node 等效语义编译到 WASM 并 ship 在 cross-origin-isolated iframe 内。读 `dist/index.d.ts:24-141` 看面:`WebContainer.boot() → mount(tree) → spawn(cmd, args) → on('server-ready', (port, url) => iframe.src = url)`。

**心智模型。** 一页面单 `WebContainer` 实例(boot 幂等-阻塞)。它拥虚拟 FS 根在 `workdir`(`fs: FileSystemAPI` 模仿 `fs.promises`,带 `readdir/readFile/writeFile/mkdir/rm/rename/watch`)。`spawn(cmd, args)` 返 `WebContainerProcess` 带 `input: WritableStream<string>, output: ReadableStream<string>, exit: Promise<number>` — 直接经 `process.output.pipeTo(new WritableStream({ write: chunk => terminal.write(chunk) }))` 经 **xterm.js** 接线。**当内部进程绑端口时,容器发 `server-ready` 带 `*.local-credentialless.webcontainer.io`(或类似)host 的唯一 URL**,你设作 `iframe.src`。

**集成 API。** 来自官方 starter:
```js
const wc = await WebContainer.boot({ coep: 'require-corp' });
await wc.mount({
  'package.json': { file: { contents: '{"name":"app","dependencies":{"express":"^4"}}' } },
  'index.js':     { file: { contents: 'import express from "express"; ...' } },
});
const install = await wc.spawn('npm', ['install']);
install.output.pipeTo(new WritableStream({ write: d => console.log(d) }));
if (await install.exit !== 0) throw new Error('install failed');
await wc.spawn('npm', ['run', 'start']);
wc.on('server-ready', (port, url) => { iframeEl.src = url; });
```

**关键约束**:host 页**必须** serve **`Cross-Origin-Embedder-Policy: require-corp`** 和 **`Cross-Origin-Opener-Policy: same-origin`**。这阻不返 `Cross-Origin-Resource-Policy` headers 的跨域 iframes & images — **对 Polynoia 一项 *显著* 迁移成本**。`coep: 'credentialless'` 模式松些(代价 cookies)。**`coep: 'none'` 只在 Chromium 经 Origin Trial 工作**。

**Polynoia 用关键功能:**
- **真 npm install** — agent 输出 `package.json`,我们真安装。**无 bundler 近似**
- **`server-ready` 事件** — Polynoia web 卡只需 `iframe.src = url`;**agent 吐什么框架(Next.js, Astro, Vite-React, SvelteKit),都跑**
- **fs.watch** — 支持 HMR。Polynoia "代码二次编辑" tab 可在每键击 `wc.fs.writeFile()`,Vite 拾起,预览刷新
- **`on('preview-message')` + 注入 `setPreviewScript()`** — 预览内部运行时错误冒出:`PreviewMessageType.UncaughtException | UnhandledRejection | ConsoleError`。**Polynoia 可作 inline error toasts 显**

**性能 / 包。** 非平凡 app 首次启动:**~3-5 秒 runtime + 10-30 秒 `npm install`(网络绑)**。冷启后,文件编辑 + HMR 亚秒。**一页面单实例** — Polynoia 跨多 chat 对话共享或 session 间 destroy/`teardown()`。

**Polynoia 启示:**
- **直接用:** agent 输出 *Next.js 或 Vite 项目* 需要 server-side rendering / API routes / hot reload 时,**这是唯一可行浏览器内选项**
- **加改造用:** 建单例 `WebContainerManager` per Polynoia session 一次 boot,经 `teardown()` + `boot()` 在对话间换 file trees(后者**昂贵**,~3 秒)
- **避开:** 静态 HTML 或简单 React 片段用 WebContainer — **Sandpack 轻 10×**
- **缺口暴露:** Polynoia 部署的 `ainotes-lp.polynoia.app` URL 模式暗示 agent 也*可能*部署 CDN。**WebContainer 精确在 *没* 部署时有用** — 短暂预览。**协议应区分 "deployed-URL preview"(iframe srcdoc/src)和 "ephemeral-WebContainer preview"(boot+mount)**
- **COEP 要求:** **最大坑**。Polynoia 主 app shell 必须 serve COEP/COOP headers,每嵌入(分析、字体、图)必须返 CORP-兼容 headers。**这是个 *破坏性* 基础设施改动**

**判定:** ◐ 加自定义包装用 — "真 Node.js" agent 输出必需,但 COEP 要求和单例 lifecycle 要求小心架构

---

## `react-diff-view` vs `@git-diff-view/react`

### `react-diff-view@3.3.3`

**Bundle:** ~45 KB gzipped + `gitdiff-parser`(5 KB) + `diff-match-patch`(~25 KB) + 可选 `refractor`(~50 KB + per-language ~5 KB)

**是什么。** 纯 React 组件集,消费**统一-diff 字符串**(`git diff` 输出)渲 side-by-side 或 unified 视图带语法高亮和 pluggable widgets。

**心智模型。** Parser(`utils/parse.ts:96 parseDiff()` → 用 `gitdiff-parser`)给你 `File[]` 带 `hunks: HunkData[]`,每 `Hunk` 有 `changes: ChangeData[]`(`type: 'insert'|'delete'|'normal'`, `content`, `oldLineNumber`, `newLineNumber`)。然后渲 `<Diff diffType={file.type} hunks={file.hunks} viewType="split"><Hunk /></Diff>`。**关键扩展点是 widgets prop** — `Record<string, ReactNode>` keyed by `getChangeKey(change)`,作 widget rows 注入到代码行间。**这是 PR-review-风 inline comments 的实现方式**。

**集成 API:**
```tsx
import { parseDiff, Diff, Hunk, tokenize } from 'react-diff-view';
import refractor from 'refractor';
const files = parseDiff(unifiedDiffString);
const tokens = tokenize(file.hunks, { highlight: true, refractor, language: 'typescript' });
<Diff
  diffType={file.type} hunks={file.hunks}
  viewType="split" tokens={tokens}
  widgets={{ [getChangeKey(change)]: <CommentEditor /> }}
  gutterEvents={{ onClick: ({change}) => toggleHunk(change) }}
  codeEvents={{ onClick: ... }}>
  {hunks => hunks.map(h => <Hunk key={...} hunk={h} />)}
</Diff>
```

Tokenize 经 `useTokenizeWorker` hook 在 worker 内跑。

**Polynoia 用关键功能:**
- **`parseDiff()` 直接给 git 输出** — 若 Polynoia diff 卡 hunks 来自 `git diff` 或格式 `@@ -X,Y +A,B @@\n+...\n-...`,解析一次调用
- **`widgets` keyed by change** — `<Apply>` 和 `<Rollback>` 按钮可放在特定 hunks 旁;AI 自然语言解释的注释在每 hunk 下
- **`gutterEvents` / `codeEvents`** — 点行选;`useChangeSelect` HOC 保 partial-apply 选状态
- **`useSourceExpansion`** — "显示 3 行" 扩展当也 feed `oldSource`
- **经 `refractor`(Prism 运行时)语法高亮** — pluggable;tokenization 在 `tokenize/toTokenTrees.ts` 走 AST 发 per-line `TokenNode[]`

### `@git-diff-view/react@0.1.4`

**Bundle:** ~80 KB gzipped(core + react) + 选 highlighter — `@git-diff-view/lowlight` 加 ~150 KB,`@git-diff-view/shiki` 加 ~400 KB(grammars 按需载)。**有 Tailwind CSS 基线**

**是什么。** 较新,多框架(React/Vue/Solid/Svelte/Angular) diff 组件。**Stateful OO 模型**:构 `DiffFile` 传给 `<DiffView />`。Parser **从 GitHub Desktop 分叉**(literal comment in `core/src/parse/diff-parse.ts:3`)。

**心智模型。** `DiffFile`(`core/src/diff-file.ts:1+`)是个 class 持 `oldFile: File, newFile: File`,解析的 hunks,语法高亮 AST 缓存,渲染方法。**模型 *更大* 更 opinionated** — `File` class 在 `core/src/file.ts:36+` 有 `ast, syntaxFile, plainFile` 字段 keyed by 行号 + 预算 HTML `template?: string`。React 层从此模型读。Widgets 经类 Zustand 内部 store 管。

**集成 API:**
```tsx
import { DiffView, DiffModeEnum } from '@git-diff-view/react';
import { generateDiffFile } from '@git-diff-view/file';
const diffFile = generateDiffFile('app.tsx', oldContent, 'app.tsx', newContent, 'tsx', 'tsx');
<DiffView
  diffFile={diffFile}
  diffViewMode={DiffModeEnum.Split}
  diffViewHighlight
  diffViewAddWidget
  renderWidgetLine={({ lineNumber, side, diffFile, onClose }) => <Editor onClose={onClose} />}
  onAddWidgetClick={(line, side) => openCommentBox(line, side)}
/>
```

**Polynoia 用关键功能:**
- **`generateDiffFile()`** — 拿旧 + 新内容 + 语言,自己算 diff(用 `diff` npm 包)。**Polynoia 有 *旧 + 新文件内容* 时有用**(非预 baked patch)
- **内置 add-widget UI:** 悬停行显 "+" 图标;click → `onAddWidgetClick(line, side)`。**比从零建简单**
- **`@git-diff-view/shiki` 集成** for 高保真语法高亮匹配 VS Code 主题
- **多选** 经 `<DiffViewWithMultiSelect />` — 跨 diff 选行范围(给"对这 5 行作一个评论"好)

### 对比 + 判定

| 方面 | `react-diff-view@3.3.3` | `@git-diff-view/react@0.1.4` |
|---|---|---|
| 成熟度 | 自 2017 成熟,许多 React app 用 | 较新, v0.1.x, 破坏改动可能 |
| Bundle | ~80 KB core(无 highlighter) | ~80 KB + 150-400 KB highlighter |
| Parser 源 | `gitdiff-parser`(专门库) | 从 GitHub Desktop 抄 |
| API 风格 | 函数/hooks/HOC | OO 模型对象 + 单 `<DiffView>` |
| 输入灵活性 | 统一-diff 字符串 → `parseDiff()` | 统一-diff hunks **或** 旧+新内容 → `generateDiffFile()` |
| Widgets / 评论 | 手动 keyed by change | 内置 add-widget 按钮 + `renderWidgetLine` callback |
| 语法高亮 | refractor (Prism) — 小 | lowlight (highlight.js) 或 shiki — 大但 VS Code parity |
| TS 类型 | 重导 `gitdiff-parser` 类型 | 自定义 `DiffFile, DiffLine, SplitSide` |
| 多框架 | 只 React | React, Vue, Solid, Svelte, Angular |

**Polynoia 采纳:**
- Polynoia diff payload 已有**结构化 hunks**(`[kind, lineNo, text]`)— **react-diff-view 数据模型 1:1 映射**(`{type: 'insert'|'delete'|'normal', content, oldLineNumber, newLineNumber}`)
- Polynoia 的 `Apply` / `Rollback` 按钮可作 **widgets per-hunk** 实现(widgets prop keyed per-change-key,我们注按钮在每 hunk *最后*行)

**Polynoia 避开:**
- `@git-diff-view/react` shiki 包太重除非已为 code-tab 需要 shiki
- `DiffFile` 模型拥可变 state — **难从 React-Query-风 server-state 架构驱动**(每 diff 消息不可变)

**判定:** ★ **用 `react-diff-view@3.3.3` 直接** for diff 消息和 PreviewPane diff tab。加薄包装给 Apply/Rollback widgets 和 per-hunk 选择。

---

## 综合(最终 stack 提案)

**编辑器:Monaco vs CodeMirror 6。** Polynoia 的 `code` 视图 tab + inline edit-then-resend,**用 Monaco Editor(+ `@monaco-editor/react`)**。CodeMirror 6(Sandpack 已拽)更小(~150 KB vs ~1-2 MB 剪后 Monaco)、有 lovely modular extensions、SSR 友好。但 Polynoia 卖点是 *重编 agent 代码* — **用户期望 IntelliSense、hover 类型、⌘+. 快速修复、熟悉的 VS Code 感**。CodeMirror 6 不 ship LSP;你要建。Monaco 开箱给所有这些 — 代价包大小,经 PreviewPane 后的 `React.lazy()` 懒载缓解。**右 pane 经 chat 卡按需开,user 已 commit 与之交互**。

**Diff 渲染器:`react-diff-view@3.3.3`**。其 `parseDiff` → `<Diff hunks={...} widgets={...} codeEvents={...} />` 流完美映射 Polynoia diff 消息形(`{file, additions, deletions, hunks: [kind, lineNo, text][]}`)。Widgets prop 让我们注 `<HunkActions onApply={} onRollback={} />` per hunk;`codeEvents.onClick` 启 "选这 hunk to partial-apply"。Diff 卡的 diff *tab* 内,我们渲同组件全尺寸带 `useSourceExpansion` 给 context 扩展。**别自滚** — diff 解析全是边缘情况(binary, rename, mode change, "no newline at end of file") `gitdiff-parser` 已处理。**别用 `@git-diff-view/react`** 除非已想 shiki 集成 for code tab。

**预览 iframe:三层策略 keyed by `card.preview.kind`:**

1. **`url`(已部署)** — 如 `ainotes-lp.polynoia.app`。**Plain `<iframe src={url} sandbox="allow-scripts allow-same-origin allow-forms" referrerPolicy="no-referrer">`**。零依赖,即载。Polynoia 现有 `web` 卡带 URL 已适合
2. **`static`(HTML/CSS/JS, 无 build)** — **`<iframe srcdoc={html} sandbox="allow-scripts" csp="default-src 'self' 'unsafe-inline'">`**。零依赖。一次性片段完美
3. **`bundle`(React/Vue/Svelte 片段,无 Node 需要)** — **`@codesandbox/sandpack-react`** 带 `template="react"|"vue"|"svelte"` + `bundlerURL` 自托管(长期)或指 CodeSandbox(短期)
4. **`fullstack`(Next.js, Astro, npm 包, dev server)** — **`@webcontainer/api`**,Polynoia tab 单例,首 `kind=fullstack` artifact 时懒 boot。**需要迁移 app shell 到 COEP `require-corp` + COOP `same-origin`**

**PreviewPane 最终 stack 提案:**
- **`tabs`:** `web | code | diff | tasks`
- **`code` tab:** `<MonacoEditor language={detectLang(filePath)} value={content} onChange={...} />` via `@monaco-editor/react`(懒载)。包:~1 MB JS + ~500 KB workers,经 `React.lazy` 控制
- **`diff` tab:** `<DiffViewer parsedFile={parseDiff(rawDiff)[0]} onApplyHunk={...} onRevertHunk={...} />` 包 `react-diff-view`。包:~120 KB 总(含 `gitdiff-parser` + `refractor` + 2 语言)
- **`web` tab:** `<PreviewIframe kind={card.preview.kind} ... />` 按 kind 调度到 4 策略之一
- **`tasks` tab:** 与此研究正交

**包大小预算:≈ 1.7 MB gzipped** for PreviewPane code-split chunk(Monaco 1 MB + Sandpack 270 KB + react-diff-view 120 KB + glue 100 KB + Sandpack 内懒 CodeMirror ≈ 150 KB)。**WebContainer 主包只加 ~30 KB** 因为运行时 iframe-hosted 外部。

**懒载策略:** `PreviewPane` 经 `React.lazy(() => import('./PreviewPane'))` 路由;内部,`code` 和 `diff` tabs 各自经 `React.lazy` 让只开 `web` 的用户不付 Monaco 代价。**WebContainer 实例仅当 `fullstack` artifact 真到达时构造,保暖到对话关闭**。
