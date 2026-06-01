# Polynoia 手动测试案例汇总

> 平时手动测验的案例,逐项可执行(操作 → ✅ 预期)。用于回归 + 答辩素材。
> 维护约定:每加/改一个功能,同步在这里加/改对应案例;废弃的功能移到文末「历史/已废弃」并保留其案例存档。

---

## 0. 环境准备

- 起服务:`make dev`(前端 :5173 + 后端 :7780)。
- 清库回初始态(测试前常用):
  - `python3 scripts/reset_clean.py --keep-contacts` —— 留联系人/适配器,清会话/工作区/消息 + 沙箱。
  - `python3 scripts/reset_clean.py`(全清,需输 `yes`)—— 回到空白基线,重新建联系人。
  - 清完**重启 server**(内存中的 adapter session / burst 状态指向已删数据)。
- 改了前端代码:浏览器**硬刷新**;改了依赖/后端:**完整重启 `make dev`**。

---

## 1. 冲突闭环(conflict closed-loop) —— 最早的核心案例

前置:一个**项目群聊**(有 workspace),里面有协调器 + ≥2 个写手联系人(见 §2 协调器)。

### TC1.1 制造冲突 + 浮现冲突卡
- 操作:给群聊发任务,让协调器**派两个写手改同一个文件的同一处**(例如都改 `app.py` 第 N 行 / 都新建同名 `VERSION.txt` 写不同内容)。
- ✅ 预期:两个写手各自在自己分支写完 → 合并 main 时第二个**失败** → 时间线出现**琥珀色冲突卡**(`待解决`,列出冲突文件 + 类型如「双新增/内容/改删/二进制」)。
- ⚠️ 注意:必须走**调度 dispatch → burst → 合并**才会触发(纯 @ 提及不触发合并阶段)。跨会话共享 workspace 残留会导致 main 已有内容、分支 ahead=0 不冲突 → 先 `reset_clean.py` + 新建群聊。

### TC1.2 解决冲突(人工)
- 操作:点冲突卡「解决冲突」→ 右栏出解决面板。
- ✅ 预期:
  - 顶部大白话说明:`{码乙} 的改动和 {码甲} 已合入 main 的版本 改到了同一处`(来源标注,不是抽象 main)。
  - 每个文件一个**折叠 diff**(长文件未变行折叠,只显示变化处)+「N 处差异」徽标 + 列图例(主线侧 / 某 agent 版本)。
  - 按钮:`采用 {码甲} / 采用 {码乙} / 手动合并`(modify_delete 是 保留/删除;binary 是整体采用一侧)。
- 操作:选一侧(或手动合并编辑)→「解决并合并」。
- ✅ 预期:卡翻绿色「已解决 → main@<sha>」;右栏代码区**自动刷新**(文件落 main)。

### TC1.3 放弃冲突
- 操作:冲突卡点「放弃」。
- ✅ 预期:卡翻「已放弃,分支未合并进 main」;不再重复浮现(dedup)。
- 并发安全:放弃与解决并发时,已合并进 main 的不会被错标 abandoned(加了 workspace 锁)。

---

## 2. 群聊协调器「自带电」(ADR-017)

- 操作:自建一个普通联系人(默认 `generalist`,无 dispatch 工具),把它指为某群聊的协调器(`orchestrator_member_id`),发个需要拆分的任务。
- ✅ 预期:它**能 dispatch 起 burst**(不依赖 persona 写没写派活指令),且物理上**没有 write 工具**(只调度不自己写)。同一联系人在别的群可当写手,互不串。

---

## 3. 文档 / PPT / 表格 预览 + 导出

准备(让 agent 产出测试文件,发群聊):
```
帮我在项目里建三个文件:
1. docs/intro.md —— 中文 Markdown,用上标题/加粗/列表/代码块/表格/引用/链接,约 300 字。
2. docs/slides.md —— Marp 幻灯,开头第一行起必须是:
   ---
   marp: true
   theme: default
   paginate: true
   ---
   再用单独一行 --- 分 6 页(封面/背景/功能/架构/进展/谢谢)。
3. data/sample.csv —— 表头「产品,单价,月销量,地区」+ 8 行中文数据。
都写进项目工作区。
```

### TC3.1 文件自动刷新
- ✅ agent 写完 + 合并进 main 后,**不手动刷新**,右栏代码区自动出现 `docs/`、`data/` 及文件,并自动打开一个。

### TC3.2 文档 WYSIWYG 编辑
- 打开 `docs/intro.md` → 预览 → ✅ Word 式排版文档(标题/列表/表格/代码块都渲染)、内容居中有页边距、可直接点字编辑。
- 改几个字 → ✅ 顶部出现「未保存」点 → Ctrl+S → 变「已保存」;切代码 tab 源码已同步。

### TC3.3 文档导出
- intro.md 预览右上角:✅ `PDF`(打印对话框→另存 PDF)/ ✅ `.md`(原文)/ ✅ `.docx`(Word 能打开,排版基本还原)。

### TC3.4 PPT 渲染 + 导出
- 打开 `docs/slides.md` → 预览 → ✅ 翻页幻灯(6 页 + 页码);代码 tab 改源码 → 预览实时变。
- ✅ `PDF`(每页一张幻灯)/ ✅ `.pptx`(PowerPoint 能打开,纯文本标题+正文,**不带 Marp 主题样式**——预期,要样式用 PDF)。

### TC3.5 表格展示 + 导出
- 打开 `data/sample.csv` → 预览 → ✅ Excel 式表格(灰底表头 + 网格)。
- ✅ `.xlsx`(Excel 能打开)/ ✅ `.csv` / ✅ `PDF`。

### TC3.6 静态 HTML 预览
- 让 agent 写一个 `index.html`(内联 CSS/JS 的简单页面)→ 打开 → 预览 → ✅ iframe 渲染出页面;✅ 可导出 `PDF` / `.html` 原文。

### TC3.7 不支持类型兜底
- 打开 `.py` → 预览 → ✅ 提示「不是文档/幻灯/网页,用代码区编辑」(不白屏不报错)。

> 导出边界(非 bug):PDF 走浏览器打印对话框;`.docx` 排版打折;`.pptx` 纯文本无主题。更高保真的 .docx/.pptx 属后端方案(后续)。

---

## 4. 改动评审(manual 合并模式)

- 把群聊 merge 模式切到 `Manual`,让 agent 改文件。
- ✅ 预期:右栏出绿/红 diff 评审面板,「接受 / 拒绝」;接受后才合并。

---

## 附录 A. reset_clean.py
见 §0。脚本会自重入 uv 环境、清 DB(保留或全清)、清沙箱 git;全清模式会重新 onboard 已装的 adapter CLI。

---

## 历史 / 已废弃(保留案例存档)

### Docker 项目运行器(2026-05-31 实现 → 2026-06-01 回滚)
- 曾做:检测 workspace 项目类型(static/npm/python)→ Docker 容器跑起来 → iframe 预览整个项目;含 import 推断装依赖、sitecustomize 强制 host/port。
- 回滚原因:对"实时渲染"太重(起容器 + 装依赖几十秒)、大项目环境千奇百怪难适配、达不到实时。改为**文档/PPT/表格前端渲染**(§3),大项目交互改走**后续的右侧终端**(见 `docs/design/right-rail-roadmap.md`)。
- 存档案例(如将来重启该方向可复用):
  - static:workspace 有 `index.html` → 起 `python -m http.server` → curl 到页面。
  - python:`app.py`(Flask)无 requirements → import 推断装 flask/flask_cors → sitecustomize 强制 `0.0.0.0:8000` → 访问首页。
  - 状态机:starting(HTTP 探测未通)→ running(探测通)→ error(容器退出,附 logs)。
