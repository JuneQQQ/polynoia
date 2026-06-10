# 图示:Polynoia 三端架构 + 单一代码源

**主题**:同一份 `apps/web/` Vite build 被三种运行时复用 — 浏览器、Tauri 桌面、Capacitor 手机。`platform.ts` 一行 detect,App.tsx 二分支 layout。

**用途**:答辩第一张图(说明工程上"为什么不做三次"),团队 onboard 时讲架构 single-source-of-truth 的依据。

## GPT-IMAGE-2 Prompt(高信息密度,16:9)

```
生成一张高信息密度的中文技术信息图海报,主题是:

《Polynoia 三端架构:Single Vite Build → Web · Tauri Desktop · Capacitor Mobile》

画布与风格:
16:9 横版构图,极浅灰背景 #FBFAF7,清晰矢量风格,论文技术海报风格,模块化网格、
紧凑标签、大数字指标。不要赛博朋克,不要机器人,不要 AI 大脑,不要人物插画。

语言要求:
图中主要文字使用简体中文。技术标识保留英文:
Vite / TypeScript / React 18 / Tauri 2 / Capacitor 6 / WKWebView / WebKitGTK /
pnpm workspace / __POLYNOIA_PLATFORM__ / window.Capacitor / window.__TAURI_INTERNALS__
isMobile() / detectPlatform() / Sidebar / PreviewPane / ChatPane / Drawer
.dmg / .app / .ipa / .aab / .apk / Xcode / Android Studio / cap sync /
cargo tauri build / pnpm cap add ios
不要生成乱码、伪中文、错别字。所有目录路径、命令、技术词必须严格按下面给出。
不要添加未在 prompt 中的新事实或新数字。

整体布局:中心一个 "apps/web/" 节点(强调色高亮),三条粗箭头辐射到三个端(顶上
左、顶上右、底中),每端再展开实现细节。底部一行"业务能力保留 vs 删减"对比表。

顶部标题栏:
大标题:Single Vite Build → Three Native Hosts
副标题:浏览器 · Tauri macOS Desktop · Capacitor iOS/Android · 业务逻辑只写一次
右侧 4 个徽章:
"pnpm workspace 单仓"
"Vite build 复用"
"platform.ts 检测 4 级优先"
"业务代码 0 复制"

中心节点 (居中,小米橙 #F2994A 边框,大号):
标题:apps/web/   (Vite + React 18 + TypeScript + Tailwind 4)
内容:
- 业务源代码:components/ / store.ts / lib/ws.ts / lib/api.ts /
              lib/platform.ts / parts/* (TextPart / ToolCallPart / 等)
- WebSocket 客户端 (lib/ws.ts) - 多 agent 并发流式渲染
- Zustand store - per-conv 状态 / agentStatus
- AI SDK 6 UIMessageChunk 解析
构建产物:apps/web/dist/  (单一 build,gzip ~675KB)

从中心节点辐射出三条粗实线,标签:

线 1 → 端 A:Browser (左上,蓝色 #5B8FF9)
端 A 内容:
- 启动:vite dev --port 7788
- 检测:detectPlatform() = "browser" (走 viewport / UA 判断)
- 布局:完整三栏 Sidebar + ChatPane + PreviewPane
- 部署:nginx static + reverse-proxy /api → uvicorn :7780

线 2 → 端 B:Tauri macOS Desktop (右上,绿色 #27AE60)
端 B 内容:
- spawn:cargo tauri build → apps/desktop/src-tauri/target/release/bundle/
- 产物:Polynoia_0.1.0_aarch64.dmg / Polynoia.app
- 加载:
    dev → http://127.0.0.1:7788 (vite dev,热重载)
    prod → 内嵌 frontendDist: ../../web/dist 静态
- 注入:window.__POLYNOIA_PLATFORM__ = "desktop" (Rust setup 阶段)
- 检测:detectPlatform() = "desktop"
- 布局:完整三栏 (跟 browser 完全一致)
- WebView:macOS 用 WKWebView (Safari 同源)
- Rust 代码:apps/desktop/src-tauri/src/main.rs ~60 行,仅启动壳
- 包体:~10 MB (vs Electron ~150 MB)

线 3 → 端 C:Capacitor iOS / Android (底中,黄色 #F2C94C)
端 C 内容:
- spawn:cap add ios + cap add android → 生成原生工程
- sync:cap sync 把 apps/web/dist 复制进
    ios/App/App/public/
    android/app/src/main/assets/public/
- 加载:
    dev → http://<LAN-IP>:7788 (--livereload --external)
    prod → bundled webDir
- 检测:detectPlatform() = "mobile" (经 window.Capacitor.isNativePlatform())
- 布局:抽屉 Sidebar (Drawer) + 全屏 ChatPane,隐藏 PreviewPane
- 产物:ios/Polynoia.ipa (Xcode Archive) + android/app-release.aab
- 容器:iOS WKWebView / Android Chrome WebView
- 插件:@capacitor/keyboard (resize=body) / status-bar / splash-screen

平台检测说明框 (中心节点正下方,紧凑):
detectPlatform() 检测优先级:
  ① 构建时注入 window.__POLYNOIA_PLATFORM__   (Tauri 主动设)
  ② window.Capacitor.isNativePlatform()       (Capacitor 运行时)
  ③ window.__TAURI_INTERNALS__                (Tauri runtime tag)
  ④ viewport (max-width:640px) + UA           (浏览器 fallback)

底部对比表 (业务能力保留 vs 删减,3 列):
                     | Web Browser | Desktop (Tauri) | Mobile (Capacitor)
─────────────────────┼─────────────┼──────────────────┼────────────────────
Sidebar 联系人列表    | 常驻        | 常驻              | 抽屉(汉堡菜单触发)
PreviewPane 产物面板  | 可开关       | 可开关            | 隐藏(屏幕小)
多 conv 切换         | ✓          | ✓                | 一次一个
Composer 输入键盘适配 | ✓          | ✓                | Keyboard plugin 自动 resize
WebSocket 流式输出   | ✓          | ✓                | ✓
多 agent 并发 + abort | ✓          | ✓                | ✓
@-mention 链         | ✓          | ✓                | ✓
TextPart 分级渲染    | ✓          | ✓                | ✓
DiffPart 完整 hunk   | ✓          | ✓                | 紧凑版 (P1)
Cmd+K 命令面板       | 浏览器原生   | 系统快捷键 (P1)   | —
推送通知            | 浏览器 API  | tauri-notification(P1) | @capacitor/push (P1)
深度链接            | URL Bar     | tauri.scheme (P1)| com.polynoia.mobile:// (P1)

底部信息条 (灰色细字):
所有端共用同一份 apps/web/dist · WS endpoint 同 127.0.0.1:7780 / 局域网 IP / TLS 域名
单仓 pnpm workspace · 业务代码改一次,三端自动同步 · 新 card 类型只在 web 加一次
当前已实现:platform.ts (33 行) / App.tsx mobile 分支 (~70 行)
Tauri shell:60 行 Rust + tauri.conf.json (50 行) · Capacitor: capacitor.config.ts (40 行)

设计要求:
中心节点 "apps/web/" 用小米橙 #F2994A 高亮(粗边 + 大字号),三条辐射线用 1.5px 实线。
三端节点各用对应颜色 (Browser #5B8FF9 / Desktop #27AE60 / Mobile #F2C94C) 的细边框。
对比表细线 #E5E7EB,✓ 用绿色 #27AE60,"隐藏/紧凑版" 用 #6B7280。
小米橙作为强调色,黑色文字 #1F2937,辅助灰 #E5E7EB。
箭头粗细分两级:从 web/ → 三端用粗箭头 (主流),
内部 sub-arrows 用细箭头 (实现细节)。
信息密度高但布局清晰,各端的 "spawn / 加载 / 检测 / 布局" 4 项垂直对齐。

Aspect ratio: 16:9.
```

## 场景说明

回答 4 个核心问题(答辩时用):

1. **为什么不每个端写一遍?**
   → 中心 `apps/web/` 高亮 + 三条辐射线显示业务逻辑 0 复制

2. **三端怎么区分?**
   → `detectPlatform()` 4 级优先级框

3. **手机端为什么"删减"?**
   → 底部对比表清晰列出 "Sidebar→抽屉 / PreviewPane→隐藏 / DiffPart→紧凑版"

4. **代码量证据?**
   → 底部信息条:Tauri 60 行 + Capacitor 40 行,业务代码全在 web

## 关联

- `CLAUDE.md` §6.3 跨平台架构(P1+ 提前到 P0/P1)
- `apps/web/src/lib/platform.ts` — 检测核心
- `apps/web/src/App.tsx` — mobile/desktop 分支
- `apps/desktop/` — Tauri 项目
- `apps/mobile/` — Capacitor 项目
- 前置图:`polynoia-multi-agent-runtime.md` 讲后端协作
- 前置图:`agent-adapter-mechanics.md` 讲 Adapter 5 大机制
