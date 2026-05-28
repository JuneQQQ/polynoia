# Polynoia 视觉方向:中文编辑学 (Chinese Editorial)

> Audit + 改造清单。提交时间:2026-05-27。
> 调用了 `frontend-design` skill 后定的方向 — 先有 vision,后逐项落地。

## 1. 方向定义 (Vision Pillars)

不是 "warm SaaS",不是 "Linear clone",不是 "Slack 风"。是 **AI 时代的编辑设计** —
把 multi-agent IM 当成"一群有文化语境的 AI 同事在你的虚拟书房里协作"。视觉参考:

- 王志弘 / 聶永真 的中文书籍装帧 — 衬线宋 + 留白 + 朴素的细线 + 偶尔一抹热色
- Stripe Press 的 web 编辑设计 — 大字号衬线 + 优雅 grid
- 早期的「The New Yorker」线上版 — 极度克制的色板,字体做活儿

**承诺**:
- 字体选择上,**中文**优先 (Noto Serif SC 当 display,IBM Plex Sans SC 当 body)
- 单一橙色作为 hot accent — 用得稀少而决断,不是大面积铺底
- 暖纸面背景 — 加微弱噪点,模拟纸张质感
- 中文标点严格(「」『』 不用 " ")
- 留白比 Tailwind 默认大 1.4 倍 — 用 vertical rhythm 区分章节,不靠 border
- 装饰元素:hair-thin 细线 (0.5px / 1px) + 单一橙色下划线作为重点
- **零**通用 SaaS 视觉(无 large blue button、无 gradient mesh、无 emoji)
- Motion 用在 conv 进入 / agent 状态 transition 上,不堆砌

## 2. Audit — 当前违反方向的地方

### 2.1 字体 (Typography)

| 位置 | 违反 | 改 |
|---|---|---|
| `index.css:48` `--font-ui` = Geist | Geist 是 Vercel default font,是"AI app 通用字体",通用化最严重 | 改 IBM Plex Sans SC 作 body |
| 无 display font | 所有标题用 sans 体,没有衬线层级 | 加 Noto Serif SC 作 display(modal title / section heading) |
| `body { font-size: 13.5px }` 太小 | 编辑设计要尺寸节奏,13.5 是 SaaS 浏览密度 | body 14px,有节奏的尺寸阶梯:11/12.5/14/16/20/28 |
| 字号散乱 | 各组件用 `text-[12.5px]` `text-[13px]` `text-[11.5px]` 随手填 | 抽象成 6 个语义 token:caption / meta / body / lead / title / display |

### 2.2 颜色 (Color)

| 位置 | 违反 | 改 |
|---|---|---|
| 暖色板基本 OK | — | 保留 #f6f2ea / #e07a3c 主色 |
| 多个 `bg-white/5` `bg-black/30` 在 sidebar | 沿用 Tailwind alpha 黑白叠加,缺乏温度 | 改用 `--color-sidebar-line` 等具名 token,色调从 sidebar 主色衍生 |
| 没有 `--color-paper-grain` | 缺少纸面 texture | 加 SVG noise 作 body `::before` overlay,opacity 0.04 |
| 橙色 `--color-accent` 偶尔被铺面 | NewContactModal 提交按钮整个橙色背景 — too loud | 主操作按钮保留小面积橙,大面积场景用 fg + hair line + hover bg-warm-tint |
| `--color-fg-3` 当 label 颜色 | 对比度 4:1 左右,在 warm bg 上看不清 | label 用 fg-2,辅助文本才用 fg-3 |

### 2.3 间距 / 节奏 (Vertical Rhythm)

| 位置 | 违反 | 改 |
|---|---|---|
| Modal 内 field 间距 `space-y-4` (16px) | 太密 | section 间 24-32px,field 内部 8-12px |
| Sidebar `space-y-0.5` 列表行间几乎贴在一起 | 失去呼吸 | 列表 row 间 4-6px,section 间 16px |
| Chat 消息无 leading 节奏 | 长段落难读 | body line-height 1.7,段间距 12px |
| 所有 modal padding `px-5 py-4` | 一致但不分体感 | 标题区 padding 大(px-6 py-5),body 适中,footer 紧 |

### 2.4 空间组合 (Spatial)

| 位置 | 违反 | 改 |
|---|---|---|
| 所有 modal 居中 + 圆角 8px | "标准 dialog" — 没特色 | modal 加 1px solid 暖深线 border + 不要 shadow + 顶部 16px 橙色 hair-thin underline 作 identity 标识 |
| Sidebar header 平铺 | 缺乏层级感 | 加 Noto Serif SC "Polynoia" + 下方一条 0.5px 橙线作 wordmark |
| Conv 切换无视觉过渡 | 突变 | 加 cross-fade 80ms 过渡 |
| Sidebar / Chat / Preview 三栏边界 1px | 太工整 | 改 0.5px + 主背景色差异区分(sidebar 暖深 / chat 米白 / preview 略浅米),不需要硬线 |

### 2.5 装饰细节 (Details)

| 位置 | 违反 | 改 |
|---|---|---|
| 标点 `"双引号"` 出现在中文文案 | 不规范 | 全部改 `「直角引号」`,`『双层直角』` |
| 所有按钮统一 `rounded` (4px) | 没特色 | 主按钮 0px(全直角),次按钮 2px,只有图标按钮 4-6px |
| 头像统一 rounded-md (6px) | OK 但平淡 | 头像保留 6px,但加 1px 内边线(同色 darken 8%)增加质感 |
| Online dot 是普通圆点 | 通用 | online dot 改成 1.5px 圆 + 0.5px 边线,更"印刷" |
| 缺乏图标层级 | lucide 图标随处 size 不一 | icon size 严格 11/13/15/18 四档,在 token 中固化 |

### 2.6 Motion

目前 motion 几乎是 0(只有 `hover:bg-white/5`)。Editorial 不需要花哨 motion,但要有:

- **modal 打开**:scale(0.96) → 1 + fade,200ms ease-out
- **conv 切换**:right pane fade 80ms
- **agent 头像 status dot**:加入心跳脉冲(在线时)— 2s pulse, 0.4 → 0.6 opacity
- **首次加载**:Sidebar items 用 staggered fade(每行 30ms delay,共 300ms),奶油白上慢慢浮出

## 3. 改造计划 (Execution Order)

按"影响面 × 改动量"分批,每批小到能 deploy 看效果:

### Batch 0 — 字体 + token 基础设施(P0,所有后续依赖这个)
- `index.css`:加 Google Fonts import (Noto Serif SC, IBM Plex Sans SC, JetBrains Mono),改 `--font-ui`,加 `--font-display`,加字号 token,加 leading token,加纸面 grain
- 验证:页面整体字体变,但不调整组件就先看排印是否正确
- 文件:`apps/web/src/index.css`,`apps/web/index.html`(preconnect Google Fonts)

### Batch 1 — Sidebar wordmark + section header
- 顶部 P logo + "Polynoia" 改 Noto Serif SC,下方一条 0.5px 橙线
- SectionHeader 字号 + 字距改 editorial 风
- 文件:`Sidebar.tsx`

### Batch 2 — Modal 重做
- NewProjectModal / NewContactModal / OnboardingModal / NewConvModal
- 移除 shadow-xl,加 0.5px 顶 hair-line 橙色 identity
- 字段 label 改 fg-2 + Noto Serif SC small caps 风
- 主按钮改 全直角 0px / 黑底白字(或 fg 底 cream 字),次按钮 改 ghost
- 文件:4 个 modal

### Batch 3 — Chat composer + bubble
- composer 输入框改无边框,只下方一根 hair-line + 聚焦后橙色 underline
- 消息 bubble 取消圆角,改 left border 2px(发送者颜色),保留 left-align
- 文件:`Composer.tsx`,`MessageView.tsx`

### Batch 4 — Motion + 微细节
- modal 进入动画(scale + fade)
- agent online dot pulse
- 首次加载 stagger
- 文件:`index.css` + 各组件

### Batch 5 — 中文标点 + 文案润色
- 全局 grep `"..."` 中文文案 → 改 「」
- 全局 grep `'...'` 同上
- Check 文案:不要 "AI agent",改 "智能体" 或 "助手"

## 4. 改完后承诺

- 不再随手填 `text-[12.5px]` — 用 `text-meta` / `text-body` / `text-lead` 等 token 类
- 不再在 Sidebar 加 `bg-white/5` 这种通用 alpha — 用 `bg-sidebar-hover` 具名 token
- 每加新组件,先想 "字号该用哪档 / 颜色该用哪档 / 该放主按钮还是 ghost"
- 中文文案严格用 「」 而非 " "

下一步:执行 Batch 0(字体 + token)— 全局影响最大,落地后整个页面会立刻有 editorial 味。
