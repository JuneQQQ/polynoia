# 右栏「代码协作 + 文档预览」:设计与演进总结

> 从「diff / 代码冲突」一路做到「文档 / PPT / 表格 / 静态页 预览 + 编辑 + 导出」的完整记录。
> 兼作 **spec**(当前系统设计)+ **dev summary**(历程与决策),也是答辩素材(rule.md AI 协作 30%)。
> 关联:`conflict-closed-loop-2026-05-30.md`、`conflict-closed-loop-CHARTER.md`、`right-rail-roadmap.md`、`docs/ADR/ADR-017-*`、`docs/testing/manual-test-cases.md`、`.skills/add-preview-renderer`。

---

## 一、演进历程(从最早到现在)

### 阶段 1 — 冲突闭环(真实 git 冲突的一等公民化)
把「多 agent 改同一处 → 静默 abort 丢弃」升级为「冻结成可解决的冲突卡 → 人工/逐块解决 → 真实重合并」。
- 后端:`probe_merge`/`conclude_merge`(workspace 共享 git,per-workspace 锁)、`ConflictPayload/Row`、conflict 端点、burst 合并挂钩。
- 前端:`ConflictPart` 卡 + `ConflictResolvePane`。
- 配套:**ADR-017 群聊协调器自带电**(被指为协调器即获得 dispatch + 注入协调协议,不靠 persona);**git 2.25.1 可移植性**(`symbolic-ref` 代替 `init -b`)。

### 阶段 2 — diff 展示优化
`@git-diff-view/core` 只渲染预算好的 hunk,不会比对两段字符串 → 自研 **LCS 行级 diff**(`diffUnified.ts`),长文件**折叠**未变行、只显示变化处;冲突面板加**来源标注**(「采用 码甲 / 采用 码乙」而非抽象 main)。
- 教训:曾做"逐块解决(conflictMarkers)",用户觉得繁琐 → **回滚**到整文件折叠 diff + 简单选择。`conflictMarkers` 删除。

### 阶段 3 — 实时渲染探索:Docker 项目运行器(做了又回滚)
为"让用户看到写出来的代码效果",曾做**整个项目跑进 Docker 容器 + iframe 预览**(检测 static/npm/python、import 推断装依赖、sitecustomize 强制 host/port)。
- **回滚原因**:太重(起容器+装依赖几十秒,谈不上实时)、大项目环境千奇百怪难适配、方向错配。
- **结论**:agent 的高频产出是**文档/PPT/表格**(文本),应前端渲染;大项目交互改走**后续右侧终端**。`runners/` 整套删除。详见 `right-rail-roadmap.md`。

### 阶段 4 — 文档 / PPT / 表格 / 静态页 预览 + 编辑 + 导出(当前)
轻量、可移植、真实时:agent 写文本,前端渲染成 Office 风格 + 导出真文件。见下「系统设计」。

---

## 二、当前系统设计(spec)

### 右栏结构
顶部「代码 / 预览」切换:
- **代码**:`CodeTab`(CodeMirror,Ctrl+S→PUT→commit,文件落 main 自动刷新 + 自动打开)。
- **预览**:`DocPreviewPane` 按文件类型分派。
- 另有 `ConflictResolvePane`(冲突)/ `DiffReviewPane`(manual 评审)按状态优先渲染。

### 文件类型分派(`DocPreviewPane.docKind`)
| 类型 | 判定 | renderer | 可编辑 | 导出 |
|---|---|---|---|---|
| 文档 | `.md`(非 marp) | `CrepeEditor`(milkdown WYSIWYG) | ✅ 所见即所得 | PDF / .md / .docx |
| 幻灯 | `.md` 带 `marp: true` 或 `.marp` | `MarpPreview`(marp-core) | 源码(代码 tab) | PDF / .pptx |
| 表格 | `.csv` / `.tsv` | `SheetPreview`(SheetJS) | 只读(MVP) | .xlsx / .csv / PDF |
| 网页 | `.html` | `HtmlPreview`(iframe) | 源码(代码 tab) | PDF / .html |
| 其它 | — | 兜底提示用代码区 | — | — |

### 数据流
- `store.openCodeFile`:CodeTab → store 单向镜像(含未保存编辑),预览据此实时渲染(Marp/HTML/表格防抖 250ms;Crepe 用 mount 时 defaultValue + 自身状态)。
- `store.workspaceFilesTick`:文件落 main(burst 合并 / resolve / Crepe 保存)→ `data-workspace-files` WS → CodeTab 重载文件树 + 同步未脏的打开缓冲。

### 导出实现(`exportUtils.ts`)
- **PDF** = 浏览器原生 print(隐藏 iframe,`printHtmlDoc`/`printAsPdf`),零依赖、渲染忠实。
- **.xlsx** = SheetJS;**.docx** = html-docx(HTML→docx);**.pptx** = pptxgenjs(从 Marp 源码解析每页 → 文本幻灯)。

---

## 三、关键决策与教训

1. **Docker 运行器回滚**:实时渲染 ≠ 跑整个项目。重型环境不可移植、起得慢。→ agent 写文本前端渲染最轻最可移植;大项目走终端。
2. **可移植优先**(用户硬约束「任何系统直接部署」):优先零依赖/纯前端(LCS diff、print-PDF、内置渲染),避免网络/宿主依赖。
3. **WYSIWYG vs 源码预览**:文档 WYSIWYG(crepe,像 Word);PPT/网页用「源码编辑 + 实时预览」(拖拽式 PPT 不现实)。
4. **简单优先**:diff 逐块解决做了觉得繁琐就回滚成整文件折叠;宁可少而稳。
5. **纯前端导出的天花板**:.docx 排版打折、.pptx 丢主题样式 → 高保真留后端(marp-cli 等)。

## 四、沉淀产物清单(答辩素材)

- **ADR**:ADR-017(协调器自带电)。
- **设计文档**:`conflict-closed-loop-2026-05-30` + `-CHARTER`、`right-rail-roadmap`、本文。
- **skill**:`.skills/add-preview-renderer`(加一种预览渲染器的标准流程)。
- **测试案例**:`docs/testing/manual-test-cases.md`(冲突/协调器/文档PPT表格/静态页 + Docker 历史存档)。
- **工具**:`scripts/reset_clean.py`(清库回初始态)。
- **自动化测试**:后端冲突/检测/orchestrator(pytest)、前端 `diffUnified` 单测(vitest)。

## 五、未竟 / 后续

- **右侧终端**(配合大型项目,见 roadmap)。
- **.docx/.pptx 更高保真**(后端 marp-cli / 专用排版)。
- **多文件静态站点**预览(相对资源,需 workspace 静态服务)。
- **表格可编辑**(当前只读)。
