# grad_defense - Design Spec

> Human-readable design narrative — rationale, audience, style, color choices, content outline.
>
> Machine-readable execution contract: `spec_lock.md`

## I. Project Information

| Item | Value |
| ---- | ----- |
| **Project Name** | grad_defense |
| **Canvas Format** | PPT 16:9 (1280×720) |
| **Page Count** | 10 |
| **Design Style** | General Consulting + academic defense |
| **Target Audience** | 硕士学位论文答辩委员会 |
| **Use Case** | 研究生毕业答辩 |
| **Created Date** | 2026-06-02 |

---

## II. Canvas Specification

| Property | Value |
| -------- | ----- |
| **Format** | PPT 16:9 |
| **Dimensions** | 1280×720 |
| **viewBox** | `0 0 1280 720` |
| **Margins** | left/right 60px, top/bottom 50px |
| **Content Area** | 1160×620 |

---

## III. Visual Theme

### Theme Style

- **Style**: General Consulting + academic defense
- **Theme**: Light theme
- **Tone**: professional, academic, modern, restrained

### Color Scheme

| Role | HEX | Purpose |
| ---- | --- | ------- |
| **Background** | `#F7FAFC` | Page background |
| **Secondary bg** | `#FFFFFF` | Card backgrounds |
| **Primary** | `#1A365D` | Titles, key sections |
| **Accent** | `#2B6CB0` | Data highlights, icons |
| **Secondary accent** | `#3182CE` | Gradient transitions |
| **Body text** | `#2D3748` | Main body text |
| **Secondary text** | `#718096` | Captions, annotations |
| **Tertiary text** | `#A0AEC0` | Footnotes, page numbers |
| **Border/divider** | `#E2E8F0` | Card borders, dividers |
| **Success** | `#38A169` | Positive indicators |
| **Warning** | `#E53E3E` | Negatives/warnings |

---

## IV. Typography System

### Font Plan

**Typography direction**: academic serif title + modern CJK sans body

| Role | Chinese | English | Fallback tail |
| ---- | ------- | ------- | ------------- |
| **Title** | `"Microsoft YaHei", "PingFang SC"` | `Georgia` | `serif` |
| **Body** | `"Microsoft YaHei", "PingFang SC"` | `Arial` | `sans-serif` |
| **Emphasis** | `SimSun` | `Georgia` | `serif` |
| **Code** | — | `Consolas, "Courier New"` | `monospace` |

**Per-role font stacks**:
- Title: `Georgia, "Microsoft YaHei", "PingFang SC", serif`
- Body: `"Microsoft YaHei", "PingFang SC", Arial, sans-serif`
- Emphasis: `Georgia, SimSun, serif`
- Code: `Consolas, "Courier New", monospace`

### Font Size Hierarchy

**Baseline**: Body font size = 20px

| Purpose | Ratio to body | Value (body=20) | Weight |
| ------- | ------------- | --------------- | ------ |
| Cover title (hero headline) | 3x | 60px | Bold |
| Page title | 1.6x | 32px | Bold |
| Subtitle | 1.2x | 24px | SemiBold |
| **Body content** | **1x** | **20px** | Regular |
| Annotation / caption | 0.75x | 15px | Regular |
| Page number / footnote | 0.55x | 11px | Regular |

### Formula Rendering Policy

Policy: `mixed` — render complex formulas as PNG; inline expressions remain editable Unicode.
Note: source contains no complex LaTeX formulas; no formula manifest needed.

---

## V. Layout Principles

### Page Structure

- **Header area**: 60px from top, page title
- **Content area**: y=100 to y=650
- **Footer area**: bottom 50px, page number + decoration

### Layout Pattern Library

| Pattern | Suitable Scenarios |
| ------- | ----------------- |
| **Single column centered** | Cover (P01), Conclusion (P09), Thanks (P10) |
| **Asymmetric split (3:7 / 2:8)** | 研究背景 (P03) — chart vs takeaway |
| **Top-bottom split** | 系统架构 (P05) — architecture diagram + description |
| **Three-column cards** | 创新点 (P04) — four innovations as cards |
| **Full-bleed + floating text** | Breathing pages |

### Spacing Specification

**Universal**:

| Element | Recommended Range | Current Project |
| ------- | ---------------- | --------------- |
| Safe margin from canvas edge | 40-60px | 60px |
| Content block gap | 24-40px | 32px |
| Icon-text gap | 8-16px | 12px |

**Card-based layouts**:

| Element | Value |
| ------- | ----- |
| Card gap | 24px |
| Card padding | 24px |
| Card border radius | 12px |
| Card width (3 cols) | 340px |

---

## VI. Icon Usage Specification

### Source

- **Built-in icon library**: `templates/icons/` — `tabler-outline`
- **Stroke width**: 2px
- **Usage**: `<use data-icon="tabler-outline/icon-name" stroke-width="2" .../>`

### Recommended Icon List

| Purpose | Icon Path | Page |
| ------- | --------- | ---- |
| 创新/灯泡 | `tabler-outline/bulb` | P04 |
| 架构/层级 | `tabler-outline/stack-2` | P05 |
| 上下文/窗口 | `tabler-outline/window` | P07 |
| 数据/图表 | `tabler-outline/chart-bar` | P08 |
| 代码/终端 | `tabler-outline/terminal-2` | P03 |
| 安全/盾牌 | `tabler-outline/shield` | P07 |
| 用户/人 | `tabler-outline/users` | P10 |
| 检查/完成 | `tabler-outline/check` | P09 |
| 速度/闪电 | `tabler-outline/bolt` | P08 |
| 链接/连接 | `tabler-outline/link` | P05 |
| 目标/靶心 | `tabler-outline/target` | P04 |
| GitHub | `tabler-outline/brand-github` | P09 |

---

## VII. Visualization Reference List

Catalog read: 71 templates

| Page | Template | Path | Summary-quote (verbatim) | Usage |
| ---- | -------- | ---- | ------------------------ | ----- |
| P05 | layered_architecture | `templates/charts/layered_architecture.svg` | "Pick for 3-4 horizontal architecture layers (presentation/service/data), 2-4 module cards per layer, each card = title + 1-line description (description required, even if source brief). Skip if no per-module descriptions (use icon_grid) or no horizontal layering (use module_composition)." | 三层架构图: Adapter层/调度层/交互层, 每层含模块卡片 |
| P08 | grouped_bar_chart | `templates/charts/grouped_bar_chart.svg` | "Pick for 2-4 series side-by-side across the same categories (e.g. YoY/QoQ). Skip if showing composition within each category (use stacked_bar_chart)." | 单/双/三Agent性能对比柱状图: 总耗时 + 测试通过率 |

**Runners-up considered** (3 entries):
- `icon_grid` | rejected for P05: layered_architecture is more specific to 3-tier architecture with per-module descriptions
- `comparison_table` | rejected for P04: 创新点 is better as visual cards with icons than a dense matrix table
- `basic_table` | rejected for P08: grouped_bar_chart visually conveys multi-agent performance contrast better than a table

---

## VIII. Image Resource List

No images — Option A (no images). Pure text + data + structural diagrams.

---

## IX. Content Outline

### Part 1: 开篇

#### P01 - Cover
- **Layout**: Single column centered, full-area accent bg
- **Rhythm**: anchor
- **Title**: 基于大语言模型的多智能体协作框架研究与实现
- **Subtitle**: Research and Implementation of LLM-based Multi-Agent Collaboration Framework
- **Info**: 答辩人: XXX / 导师: XXX 教授 / 2026年6月

#### P02 - 目录
- **Layout**: Vertical list with numbers
- **Rhythm**: anchor
- **Title**: 目录
- **Content**: 7 sections listed: 研究背景 → 系统架构 → 适配器层 → 上下文预算 → 冲突闭环 → 实验评估 → 总结展望

### Part 2: 研究背景

#### P03 - 研究背景与问题
- **Layout**: Asymmetric split — left 40% (bottlenecks list), right 60% (research challenges cards)
- **Rhythm**: dense
- **Title**: 研究背景与问题定义
- **Content**: 三大瓶颈 (上下文窗口/角色固化/并行受限) + 三大挑战 (任务拆解/文件冲突/上下文管理)

#### P04 - 研究内容与创新点
- **Layout**: 4-column card grid (2×2)
- **Rhythm**: dense
- **Title**: 研究内容与创新点
- **Content**: 4 innovations as icon cards: 统一适配器架构 / 四层上下文预算 / 冲突闭环机制 / IM协作界面

### Part 3: 系统设计

#### P05 - 系统总体架构
- **Visualization**: `layered_architecture`
- **Layout**: Top-bottom — diagram on top, key metrics below
- **Rhythm**: breathing
- **Title**: 系统总体架构
- **Content**: 三层架构图: Adapter Layer / Scheduling Layer / Interaction Layer, 附17种MessagePart说明

#### P06 - 三层协议设计
- **Layout**: 3-column cards
- **Rhythm**: dense
- **Title**: 三层协议设计
- **Content**: PAP协议 / AI SDK 6 / REST+WebSocket 各一栏, 含数据方向与用途

### Part 4: 关键技术

#### P07 - 上下文预算引擎
- **Layout**: 2×2 quadrant cards with a center concept
- **Rhythm**: dense
- **Title**: 四层上下文预算模型
- **Content**: 系统层15% / 历史层30% / 工具层40% / 扩散层15%, 配token节省率数据

#### P08 - 性能评估
- **Visualization**: `grouped_bar_chart`
- **Layout**: Left chart + right KPI cards
- **Rhythm**: dense
- **Title**: 实验评估与性能对比
- **Content**: 单/双/三Agent对比柱状图 + 消融实验结果

### Part 5: 收尾

#### P09 - 工作总结与贡献
- **Layout**: 4-item vertical list with checkmarks
- **Rhythm**: breathing
- **Title**: 研究工作总结
- **Content**: 四大贡献 + 未来工作方向

#### P10 - 致谢
- **Layout**: Single column centered
- **Rhythm**: anchor
- **Title**: 感谢聆听
- **Subtitle**: Thank You
- **Content**: 导师致谢 + 实验室致谢 + Q&A

---

## X. Speaker Notes Requirements

One speaker note file per page, saved to `notes/`:
- Filename: match SVG name (e.g. `01_cover.md`)
- Style: formal academic presentation, 3-5 key points per page
- Duration: ~15 minutes total

---

## XI. Technical Constraints Reminder

### SVG Generation Must Follow:
1. viewBox: `0 0 1280 720`
2. Background uses `<rect>` elements
3. Text wrapping uses `<tspan>` (`<foreignObject>` FORBIDDEN)
4. Transparency uses `fill-opacity` / `stroke-opacity`; `rgba()` FORBIDDEN
5. FORBIDDEN: `mask`, `<style>`, `class`, `foreignObject`, `textPath`, `animate*`, `script`
6. Unicode characters: raw Unicode (em dash `—`, arrow `→`); XML reserved chars escaped: `&amp;` `&lt;` `&gt;`
7. `<g opacity="...">` FORBIDDEN (set opacity on each child individually)
8. `clipPath` conditionally allowed only on `<image>` elements
