# @polynoia/desktop

Polynoia desktop client built with Tauri 2. It reuses `apps/web/dist` for the UI
and adds native desktop backend management.

## Backend Selection

The desktop app exposes two user-facing choices:

1. **Desktop embedded backend**
   - Default for packaged macOS builds.
   - Tauri starts a bundled server resource on a random `127.0.0.1:<port>`.
   - The port is injected into the web app as the desktop backend URL.
   - Data is isolated under the desktop app data directory, so it does not share
     the web dev database by accident.

2. **Custom backend**
   - Connects to a LAN / remote URL such as `http://10.2.255.109:7780`.
   - Also covers an intentionally shared local dev server: enter
     `http://127.0.0.1:7780` if the desktop app should use the same server/data
     as the web dev app.

This avoids the old ambiguity where desktop and web both tried to own `7780`.
Web dev still uses:

```text
frontend: http://127.0.0.1:7788
backend:  http://127.0.0.1:7780
```

Packaged desktop uses:

```text
frontend: bundled web/dist, no frontend port
backend:  random localhost port managed by Tauri
```

## Architecture

```text
Polynoia.app
├─ Tauri WebView
│  ├─ dev:  http://127.0.0.1:7788
│  └─ prod: bundled apps/web/dist
├─ DesktopBackend manager
│  ├─ picks a free localhost port
│  ├─ starts bundled server resource with isolated POLYNOIA_* paths
│  └─ exposes desktop_backend_status / start_desktop_backend commands
└─ web runtime-config
   ├─ desktop embedded backend
   └─ custom backend
```

The backend exposes `/api/identity`, so the UI can show whether it is connected
to the embedded desktop backend or a custom server.

## Development

From the repository root:

```bash
pnpm install
make server        # optional shared backend on :7780
pnpm dev:desktop   # starts Vite on :7788 and opens the Tauri window
```

In dev mode the Tauri app can still start an embedded backend from
`apps/server/` if `uv` is available. If that fails, switch the server setting to
**Custom backend**, enter `http://127.0.0.1:7780`, and run `make server`.

## Build macOS App / DMG

```bash
pnpm build:desktop
```

The Tauri `beforeBuildCommand`:

1. builds `apps/web/dist`;
2. runs `scripts/prepare_desktop_server_resource.sh`;
3. copies a lightweight backend resource into `src-tauri/resources/server`;
4. bundles that resource into the app.

The resource intentionally excludes `.venv`, SQLite databases, caches, and build
artifacts. At runtime the desktop app creates its own uv environment under the
app data directory.

Outputs:

```text
apps/desktop/src-tauri/target/release/bundle/dmg/Polynoia_0.1.0_aarch64.dmg
apps/desktop/src-tauri/target/release/bundle/macos/Polynoia.app
```

## Notes

- The embedded backend currently requires `uv` to be available on the machine.
  If it is not, the UI surfaces the failure and the user can choose a custom
  backend. A fully standalone Python sidecar binary is the next packaging
  hardening step.
- The desktop embedded backend uses its own data directory; enter
  `http://127.0.0.1:7780` as a **Custom backend** if you deliberately want
  desktop and web to see the same conversations.
