# @polynoia/mobile

Polynoia 手机端(iOS / Android,经 Capacitor 6)。**共用 @polynoia/web 的代码**,通过 `apps/web/src/lib/platform.ts` 的 runtime detection 切到 mobile 布局(Sidebar 变抽屉,PreviewPane 隐藏)。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│   iOS / Android App (Capacitor 6)                       │
│   ┌──────────────────────────────────────────────────┐  │
│   │  Native WebView (WKWebView iOS / Chrome Android) │  │
│   │   ↓ 加载                                          │  │
│   │   ① dev livereload: http://<LAN-IP>:5173         │  │
│   │   ② prod:bundled apps/web/dist (synced into     │  │
│   │      ios/App/App/public 和 android/.../public)   │  │
│   │  自动注入 window.Capacitor → platform.ts 检测     │  │
│   │  到 → isMobile()=true → 单列 + 抽屉 sidebar      │  │
│   └──────────────────────────────────────────────────┘  │
│                            │                             │
│                            ↓  HTTP/WS                    │
│              Polynoia server (LAN IP : 7780)             │
└─────────────────────────────────────────────────────────┘
```

## 前置

```bash
# macOS 装 Xcode (iOS) + Android Studio (Android)
xcode-select --install                                # iOS
# Android Studio: https://developer.android.com/studio

# Node 18+ + pnpm
node -v && pnpm -v

# Capacitor CLI 装到 monorepo
cd /path/to/polynoia
pnpm install
```

## 首次初始化(每台开发机一次)

```bash
cd apps/mobile

# 1. 装依赖
pnpm install

# 2. 先 build web(Capacitor sync 需要 dist)
pnpm build:web

# 3. 添加原生工程(选其一或两个)
pnpm cap add ios          # 产生 apps/mobile/ios/
pnpm cap add android      # 产生 apps/mobile/android/

# 4. 同步 web build 进原生工程
pnpm cap sync
```

## Dev livereload(手机连同一 WiFi)

```bash
# 终端 1: Polynoia server
cd apps/server
uv run uvicorn polynoia.main:app --host 0.0.0.0 --port 7780  # 必须 0.0.0.0

# 终端 2: vite dev
cd apps/web
./node_modules/.bin/vite --host 0.0.0.0 --port 5173

# 终端 3: launch phone(替换 LAN IP)
cd apps/mobile
# iOS
cap run ios --livereload --external --port=5173
# Android(USB 调试或模拟器)
cap run android --livereload --external --port=5173
```

手机 WebView 自动加载 `http://<电脑LAN-IP>:5173`,改 web 代码自动刷新。

## Build 生产 IPA / APK

```bash
cd apps/mobile

# 同步最新 web build
pnpm sync

# iOS — 打开 Xcode 选证书 + Archive → 上 TestFlight
pnpm open:ios

# Android — 打开 Android Studio → Build → Generate Signed Bundle
pnpm open:android
# 或命令行:cd android && ./gradlew assembleRelease
```

## 关键文件

| 文件 | 作用 |
|---|---|
| `package.json` | Capacitor 6 deps + 脚本 |
| `capacitor.config.ts` | webDir 指向 web/dist, plugins 配置 |
| `ios/` | Xcode 工程(`cap add ios` 生成) |
| `android/` | Gradle 工程(`cap add android` 生成) |
| `../web/dist/` | 业务代码源(共享) |

## Mobile 布局适配

完全在 web 代码里,无需 mobile-specific code:

```typescript
// apps/web/src/App.tsx
import { isMobile } from "./lib/platform";

const mobile = isMobile();  // detects via window.Capacitor

if (mobile) {
  // Hamburger menu, drawer Sidebar, no PreviewPane, full-screen ChatPane
}
```

`platform.ts` 检测优先级:Capacitor runtime → Tauri runtime → 视口/UA。

## 已知 P1+ 工作

- 推送通知(`@capacitor/push-notifications`):agent 完成/abort/出错 push
- 系统通知(`@capacitor/local-notifications`)
- 本地存 token / theme(`@capacitor/preferences`)
- iOS 启动屏图 + 角标(目前用占位)
- Android 状态栏颜色根据 theme 切换
- Deep link(`com.polynoia.mobile://conv/<id>` 跳到指定对话)
- 离线 conv 列表缓存

## 已知限制(对比桌面/网页)

- ❌ PreviewPane(右侧产物面板)— 屏幕太小
- ❌ Sidebar 常驻 → 改抽屉,从左滑出
- ❌ 多 conv 并行展示 → 一次只显示一个 conv
- ❌ 复杂 diff 卡 — 用紧凑版本
- ✅ 流式输出 + Markdown 渲染 — 完全保留
- ✅ 多 agent 并发 + abort — 完全保留(状态条 chip 横滚)
- ✅ @-mention 链 — 完全保留
- ✅ 音频/图片附件(P1+ via `@capacitor/camera`)
