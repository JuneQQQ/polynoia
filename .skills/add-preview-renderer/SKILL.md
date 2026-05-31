---
name: add-preview-renderer
description: 给右栏「预览」tab 加一种文件类型的渲染器(展示/编辑 + 导出)
---

# add-preview-renderer

给右栏「预览」支持一种新文件类型——按文件后缀/内容分派到一个渲染组件,展示(或所见即所得编辑),并提供导出。已用此流程做了 4 个:`CrepeEditor`(.md 文档 WYSIWYG)、`MarpPreview`(Marp 幻灯)、`SheetPreview`(.csv 表格)、`HtmlPreview`(.html 静态页)。

## 何时用

- 要预览/编辑一种**新文件类型**:`.vue`、`.json`、`.geojson`、图片、Mermaid、`.ipynb` 等。
- agent 产出的是**文本**(前端能渲染),想让用户看到排版好的效果并导出。

## 不该用

- 只是调现有 renderer 的样式 → 直接改那个组件。
- 要"把整个项目跑起来"(npm dev / 后端服务)→ 那是**右侧终端**方向(见 `docs/design/right-rail-roadmap.md`),不是预览渲染器。Docker 跑项目已回滚,别复活。
- 要渲染**二进制 Office 文件**(.docx/.xlsx 原文件)→ 需要 raw-bytes fetch(当前 `openCodeFile.content` 是 UTF-8 文本,二进制会损坏),先评估。

## 步骤

### 1. 定文件类型 + 判定
在 `apps/web/src/components/preview/DocPreviewPane.tsx` 的 `docKind(path, content)` 决定怎么识别(后缀,必要时看内容,如 Marp 靠 `marp: true` front-matter)。

### 2. (如需)装渲染库
`pnpm add <lib>`。优先**纯前端、可离线**的库(可移植硬约束)。

### 3. 写 `XxxPreview.tsx`
`apps/web/src/components/preview/XxxPreview.tsx`,约定:
- props:`{ content: string; fileName?: string }`(`content` 来自 `store.openCodeFile`,代码 tab 编辑会实时镜像过来)。
- 顶部 toolbar:文件名 + 导出按钮(复用 `exportUtils`)。
- 渲染区填满。错误兜底(渲染失败显示错误,不白屏)。
- **可编辑的**(如 crepe):保存 = `api.workspaceFileWrite` + `useStore.getState().bumpWorkspaceFiles()`(让 CodeTab 同步,防 stale 覆盖)。

### 4. 在 `DocPreviewPane` 分派
`docKind` 加分支 + render 加 `if (kind === "xxx") return <XxxPreview content={...} fileName={...} />;`。
- 实时预览(只读)用 `debounced` content;WYSIWYG 编辑器用 `file.content` + `key={file.path}`。

### 5. 导出复用 `exportUtils.ts`
`downloadBlob` / `downloadText` / `printAsPdf`(片段)/ `printHtmlDoc`(完整 html 文档)/ `csvToXlsxBlob`。新格式(如 docx/pptx)在组件里用对应库生成 Blob → `downloadBlob`。

### 6. 验证
`./node_modules/.bin/tsc -b` + 实测:打开该类型文件 → 切「预览」→ 看渲染、编辑、各导出按钮。必要时给纯函数(解析/转换)加 vitest(参考 `diffUnified.test.ts`)。

## 关键陷阱

- **content 是文本**:`workspaceFileRead` 返回 UTF-8;二进制文件(.xlsx/.docx)直接读会乱码 → 二进制要 raw fetch(后续)。
- **WYSIWYG 别回灌 content**:milkdown crepe 只在 mount 读 `defaultValue`,之后自己持有状态;把 `content` 当受控 prop 回灌会跟光标打架。切文件用 `key={path}` 重挂,**StrictMode 双挂载**要 `cancelled` 守卫(别销毁半成品 editor)。
- **双编辑器同步**:可编辑 renderer 与 CodeTab 编辑同一文件 → 保存后 `bumpWorkspaceFiles`,CodeTab 才会重读、不会用旧缓冲覆盖。
- **iframe 沙箱**:渲染用户内容用 `sandbox="allow-scripts"`,**别加 `allow-same-origin`**(两者同时会让沙箱失效)。
- **PDF 导出**:完整 `<html>` 文档(如 .html 页)用 `printHtmlDoc`(保留其 head/style);body 片段(md 渲染、表格)用 `printAsPdf(body, title, "<style>…</style>")`。
- **纯前端导出天花板**:`.docx` 排版会打折、`.pptx` 丢主题样式;高保真留后端,UI 上如实标注。
