# Polynoia 品牌图标资产

平台感知的 Polynoia 图标,来源于 Claude Design 交付件 **「Polynoia 图标 9版」**
(`icon-art-v2.jsx` 里的 `PNIcon`)。三个设计方向 × 三种平台原生处理:

| 方向(列) | key | 长相 |
|---|---|---|
| 第一列 · 字标 P | `mono` | 暖橙底 + 米色「P」 |
| 第二列 · 三色交叠 | `triad` | 三个 agent 身份色(橙/青/紫)multiply 交叠 |
| 第三列 · 多节点 | `nodes` | 三节点连成网 |

## 平台 → 方向(产品决策)

> 决策人:用户(2026-05-31)。来源对话:设计交付 chat2「应用图标设计」。

| 用途 | 平台处理 | 方向 | 文件 |
|---|---|---|---|
| **网页端 favicon** | web(扁平,小尺寸可读) | `mono` 第一列 | `apps/web/public/favicon.svg`(线上) · `favicon-web-mono.svg`(母版) |
| **桌面端 app 图标** | macOS squircle + 景深 + 高光 | `triad` 第二列 | `icon-desktop.svg` |
| **手机端 app 图标** | iOS squircle + 高光(字形放大 1.08) | `triad` 第二列 | `icon-mobile.svg` |
| **应用内品牌 logo** | web 扁平圆角块 | `triad` 第二列 | `logo.svg`(母版,侧栏由 `BrandIcon` 运行时渲染) |

一句话:**favicon 用「P」字标,其余(桌面/手机 app 图标 + 应用内 logo)都用三色交叠。**

## 两份实现,保持同步

- **运行时**:`apps/web/src/components/BrandIcon.tsx` —— React 组件,侧栏品牌标记在用。
- **静态文件**:`scripts/gen_brand_icons.py` —— 生成本目录下的 SVG 母版 + 线上 favicon。
  改了配色/几何后重跑:`python3 scripts/gen_brand_icons.py`。

桌面端(`apps/desktop`,Tauri)与手机端(`apps/mobile`,React Native)是 P1+ 才建的,
届时它们的图标流水线(`.icns` / `AppIcon` 集)直接消费这里的 `triad` 母版即可。

## 待办(P1+,真正出 app 时)

- [ ] 由 `icon-desktop.svg` 导出 macOS `.icns`(多尺寸 PNG → iconutil)
- [ ] 由 `icon-mobile.svg` 导出 iOS `AppIcon` 全尺寸集
- [ ] favicon 补 `.ico` 多尺寸 + `apple-touch-icon` + web manifest
