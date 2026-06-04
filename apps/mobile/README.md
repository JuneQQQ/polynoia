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

## 连接服务器(首次启动)

手机**不跑本地后端** —— 它连接一台远程 Polynoia server。首次启动会出现
**连接服务器界面**(`ConnectServerScreen`):

1. 填服务器地址(局域网 IP / 域名,如 `http://10.2.255.109:7780`)
2. 「测试连接」(打 `GET /api/agents`)→ 显示 ✓ N 个 agent
3. 「连接」→ 地址持久化(原生用 `@capacitor/preferences`,见 `lib/storage.ts`)+ 重载

之后可在抽屉底部齿轮(服务器设置)里改地址。HTTP/WS 全部走
`lib/runtime-config.ts` 的 `getServerHttpBase()` / `getServerWsBase()`(WebView 下
**绝不**回退到 `window.location`)。

### 服务器端必须放行 CORS

WebView 跨源调用后端,server 的 `cors_origins`(`apps/server/polynoia/settings.py`)
已加 `https://localhost` / `capacitor://localhost` / `http://localhost`。**真机首次连接
若失败**,从 server 访问日志读真实 `Origin`,确认在白名单里(可用
`POLYNOIA_CORS_ORIGINS` 覆盖)。后端须 `--host 0.0.0.0` 才能被手机访问。

> iOS 还需在开发机装 CocoaPods(`sudo gem install cocoapods` 或 `brew install
> cocoapods`),首次 `cap add ios` 时若未装会跳过 `pod install`,在 Xcode 里补跑即可。

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

## 已完成(本轮)

- ✅ 连接服务器界面 + 远程后端连通(`ConnectServerScreen` + `runtime-config`)
- ✅ 持久化走 `@capacitor/preferences`(`lib/storage.ts`,server 地址等关键值)
- ✅ 状态栏配色 / SplashScreen / 键盘(`lib/native.ts:initNative`)+ 安全区适配
- ✅ 后台/前台恢复 + 网络恢复 → WS 自动重连(`@capacitor/app` + `@capacitor/network`)
- ✅ 移动端裁剪到 IM 子集(隐藏新建项目;文件树/终端/提交历史本就不挂载)
- ✅ 后端 CORS 放行 Capacitor 源

## 已知 P1+ 工作

- 推送通知(`@capacitor/push-notifications`):agent 完成/abort/出错 push
- 系统通知(`@capacitor/local-notifications`)
- iOS 启动屏图 + 角标(目前用占位)
- Deep link(`com.polynoia.mobile://conv/<id>` 跳到指定对话)
- 离线 conv 列表缓存
- 审批改为原生底部 Sheet(v1 沿用悬浮条)

## 已知限制(对比桌面/网页)

- ❌ PreviewPane(右侧产物面板)— 屏幕太小
- ❌ Sidebar 常驻 → 改抽屉,从左滑出
- ❌ 多 conv 并行展示 → 一次只显示一个 conv
- ❌ 复杂 diff 卡 — 用紧凑版本
- ✅ 流式输出 + Markdown 渲染 — 完全保留
- ✅ 多 agent 并发 + abort — 完全保留(状态条 chip 横滚)
- ✅ @-mention 链 — 完全保留
- ✅ 音频/图片附件(P1+ via `@capacitor/camera`)
