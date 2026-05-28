# @polynoia/desktop

Polynoia 桌面客户端(Tauri 2)。**完全复刻 @polynoia/web 业务逻辑**,只是把它装进原生窗口。

## 架构

```
┌──────────────────────────────────────────────────────────┐
│  Polynoia Desktop App (Tauri 2)                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Native WebView (macOS WKWebView / Linux WebKitGTK) │  │
│  │   ↓ 加载                                            │  │
│  │   ① dev: http://127.0.0.1:5173 (vite dev)          │  │
│  │   ② prod: ../../web/dist (vite build static)       │  │
│  │ 注入:window.__POLYNOIA_PLATFORM__ = "desktop"       │  │
│  └────────────────────────────────────────────────────┘  │
│                       │                                   │
│                       ↓ HTTP/WS                           │
│              ┌──────────────────┐                         │
│              │ Polynoia Server  │                         │
│              │ uvicorn :7780    │                         │
│              └──────────────────┘                         │
└──────────────────────────────────────────────────────────┘
```

业务逻辑 100% 在 `apps/web/` — 这里的 `src-tauri/` 只是几十行 Rust 启动代码。

## 前置(macOS)

```bash
# 1. 装 Rust 工具链
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# 2. 装 Tauri CLI
cargo install tauri-cli --version "^2.1"

# 3. 装 web 端依赖(monorepo 根)
cd /path/to/polynoia
pnpm install                 # 安装 apps/web 和 apps/desktop 的 node deps

# 4. 启动 Polynoia server(另一个终端)
cd apps/server
uv sync && uv run uvicorn polynoia.main:app --host 127.0.0.1 --port 7780
```

## Dev 模式(macOS)

```bash
cd apps/desktop
pnpm tauri dev
```

会自动:
1. 起 `vite dev` 监听 5173
2. spawn Rust 二进制开一个原生窗口加载 `http://127.0.0.1:5173`
3. 热重载:改 web 代码 → 窗口自动刷新

## Build .dmg / .app(macOS)

```bash
cd apps/desktop
pnpm tauri build
# 产物在 src-tauri/target/release/bundle/
#   dmg/Polynoia_0.1.0_aarch64.dmg
#   macos/Polynoia.app
```

Apple silicon 默认 `aarch64`,Intel 加 `--target x86_64-apple-darwin`。两端通吃用 `--target universal-apple-darwin`(需先 `rustup target add aarch64-apple-darwin x86_64-apple-darwin`)。

## Build Linux .deb / .AppImage

```bash
# 需要 libwebkit2gtk-4.1-dev libgtk-3-dev
sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev
pnpm tauri build
```

## 文件结构

```
apps/desktop/
├── package.json              # Tauri CLI 入口 + 脚本
├── README.md                 # 本文件
└── src-tauri/                # Rust 部分(瘦)
    ├── Cargo.toml            # Rust 依赖
    ├── build.rs              # tauri-build 钩子
    ├── tauri.conf.json       # 窗口/bundle/CSP 配置
    ├── capabilities/
    │   └── default.json      # Tauri 2 capability 系统(权限白名单)
    ├── icons/                # macOS/Win/Linux 图标(占位,P1 替换)
    └── src/
        └── main.rs           # 60 行启动代码
```

## 为什么用 Tauri 而不是 Electron

- **包体积**:Tauri ~10MB vs Electron ~150MB
- **内存**:Tauri 用 OS 自带 webview,无独立 Chromium
- **安全**:capability 系统比 Electron 的全 Node 暴露更严
- **macOS 原生窗口感**:WKWebView 跟 Safari 同源

## 已知 P1+ 工作

- icons/ 目录需要替换占位为正式品牌图标(`tauri icon`)
- 加 macOS Touch Bar / Dock Menu
- App auto-update via Tauri Updater
- 系统通知(agent 完成 / abort)
- 全局快捷键(Cmd+K 打开命令面板)
