// Prevent additional console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::{
    ffi::{OsStr, OsString},
    fs,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, State, WindowEvent,
};

// Windows: spawn console children without allocating a visible console window.
// A GUI parent (our app) spawning `uv`/adapters otherwise pops a window the user
// can close — killing the backend. 0x0800_0000 = CREATE_NO_WINDOW.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Clone, Debug, Serialize)]
struct BackendInfo {
    mode: String,
    status: String,
    url: Option<String>,
    pid: Option<u32>,
    message: String,
}

#[derive(Debug)]
struct BackendInner {
    info: BackendInfo,
    child: Option<Child>,
}

#[derive(Debug)]
struct DesktopBackend {
    data_dir: PathBuf,
    // Where Tauri unpacked bundle resources (the Python source + bundled uv).
    // None in odd setups; discovery then falls back to exe-relative + dev tree.
    resource_dir: Option<PathBuf>,
    inner: Mutex<BackendInner>,
}

impl Drop for DesktopBackend {
    fn drop(&mut self) {
        if let Ok(mut inner) = self.inner.lock() {
            if let Some(child) = inner.child.as_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

#[tauri::command]
fn desktop_backend_status(state: State<'_, DesktopBackend>) -> BackendInfo {
    state.inner.lock().expect("backend mutex").info.clone()
}

#[tauri::command]
fn start_desktop_backend(state: State<'_, DesktopBackend>) -> BackendInfo {
    ensure_embedded_backend(&state)
}

// Idempotent: launch the embedded backend if it isn't already coming up, and
// report where it stands. The web layer polls this until `status == "running"`.
//
// Crucial first-run behavior: on a fresh machine `uv run` must download a Python
// + ~100 wheels (minutes). We therefore NEVER kill a still-alive child just
// because health didn't pass within the short probe window — we report
// "starting" and let the next poll re-check. Killing mid-sync (the old behavior)
// corrupted the venv and made the app permanently unable to boot.
fn ensure_embedded_backend(state: &DesktopBackend) -> BackendInfo {
    let mut inner = state.inner.lock().expect("backend mutex");

    // Already have a child? Re-check its liveness + health instead of spawning
    // a second one.
    if let Some(child) = inner.child.as_mut() {
        match child.try_wait() {
            Ok(None) => {
                // Alive. One quick health probe — flip to running the moment the
                // server answers; otherwise stay "starting" (deps still installing).
                let port = inner.info.url.as_deref().and_then(parse_port);
                let healthy = port.map(probe_health).unwrap_or(false);
                if healthy {
                    let url = inner.info.url.clone();
                    inner.info = BackendInfo {
                        mode: "desktop_embedded".into(),
                        status: "running".into(),
                        url,
                        pid: inner.info.pid,
                        message: "桌面内置后端已启动".into(),
                    };
                }
                return inner.info.clone();
            }
            Ok(Some(status)) => {
                // Exited — surface where the log is so failures are diagnosable.
                let log = self_log_path(&state.data_dir);
                inner.info = BackendInfo {
                    mode: "desktop_embedded".into(),
                    status: "error".into(),
                    url: None,
                    pid: None,
                    message: format!("内置后端已退出: {status}。日志: {}", log.display()),
                };
                inner.child = None;
            }
            Err(err) => {
                inner.info = BackendInfo {
                    mode: "desktop_embedded".into(),
                    status: "error".into(),
                    url: None,
                    pid: None,
                    message: format!("无法检查内置后端状态: {err}"),
                };
                inner.child = None;
            }
        }
    }

    let port = match reserve_local_port() {
        Ok(p) => p,
        Err(err) => {
            inner.info = BackendInfo {
                mode: "desktop_embedded".into(),
                status: "unavailable".into(),
                url: None,
                pid: None,
                message: format!("无法分配本机端口: {err}"),
            };
            return inner.info.clone();
        }
    };
    let url = format!("http://127.0.0.1:{port}");
    let mut cmd = match backend_command(port, &state.data_dir, state.resource_dir.as_deref()) {
        Ok(c) => c,
        Err(err) => {
            inner.info = BackendInfo {
                mode: "desktop_embedded".into(),
                status: "unavailable".into(),
                url: None,
                pid: None,
                message: err,
            };
            return inner.info.clone();
        }
    };

    let child = match cmd.spawn() {
        Ok(c) => c,
        Err(err) => {
            inner.info = BackendInfo {
                mode: "desktop_embedded".into(),
                status: "error".into(),
                url: None,
                pid: None,
                message: format!("内置后端启动失败: {err}"),
            };
            return inner.info.clone();
        }
    };
    let pid = child.id();
    inner.info = BackendInfo {
        mode: "desktop_embedded".into(),
        status: "starting".into(),
        url: Some(url.clone()),
        pid: Some(pid),
        message: "内置后端正在启动(首次启动需联网安装依赖,可能数分钟)".into(),
    };
    inner.child = Some(child);
    drop(inner);

    // Short readiness window: a warm venv answers in ~5s. If it doesn't (cold
    // first run still installing), leave the child running and report "starting"
    // — the web poller will pick up "running" on a later tick. Never kill here.
    let ready = wait_for_health(port, Duration::from_secs(15));
    let mut inner = state.inner.lock().expect("backend mutex");
    // Only promote to running if the child is still the one we launched.
    let still_alive = inner
        .child
        .as_mut()
        .map(|c| matches!(c.try_wait(), Ok(None)))
        .unwrap_or(false);
    if ready && still_alive {
        inner.info = BackendInfo {
            mode: "desktop_embedded".into(),
            status: "running".into(),
            url: Some(url),
            pid: Some(pid),
            message: "桌面内置后端已启动".into(),
        };
    } else if !still_alive {
        let log = self_log_path(&state.data_dir);
        inner.info = BackendInfo {
            mode: "desktop_embedded".into(),
            status: "error".into(),
            url: None,
            pid: None,
            message: format!("内置后端启动失败,请查看日志: {}", log.display()),
        };
        inner.child = None;
    }
    // else: alive but not yet healthy → keep "starting" (set above).
    inner.info.clone()
}

fn reserve_local_port() -> Result<u16, String> {
    TcpListener::bind(("127.0.0.1", 0))
        .map_err(|e| e.to_string())
        .and_then(|listener| listener.local_addr().map_err(|e| e.to_string()).map(|a| a.port()))
}

fn parse_port(url: &str) -> Option<u16> {
    url.rsplit(':').next().and_then(|p| p.parse().ok())
}

fn self_log_path(data_dir: &Path) -> PathBuf {
    data_dir.join("embedded").join("backend.log")
}

fn backend_command(
    port: u16,
    data_dir: &Path,
    resource_dir: Option<&Path>,
) -> Result<Command, String> {
    fs::create_dir_all(data_dir).map_err(|e| format!("无法创建桌面数据目录: {e}"))?;
    let instance_id = format!("desktop-{}-{port}", now_ms());
    let home = data_dir.join("embedded");
    fs::create_dir_all(&home).map_err(|e| format!("无法创建内置后端数据目录: {e}"))?;

    let mut cmd = if let Ok(template) = std::env::var("POLYNOIA_DESKTOP_SERVER_CMD") {
        let rendered = template.replace("{port}", &port.to_string());
        let mut c = Command::new("sh");
        c.arg("-lc").arg(rendered);
        c
    } else if let Some(sidecar) = find_bundled_server(resource_dir) {
        let mut c = Command::new(sidecar);
        c.arg("--host").arg("127.0.0.1").arg("--port").arg(port.to_string());
        c
    } else if let Some(server_dir) = find_server_dir(resource_dir) {
        let uv = uv_program(resource_dir);
        uvicorn_command(server_dir, port, &uv)
    } else {
        return Err(
            "未找到内置后端(既无打包资源也无开发目录 apps/server)。请切换到本机共享后端或自定义后端。"
                .into(),
        );
    };

    // Hide the backend's own console window on Windows (the Python global patch in
    // polynoia/__init__.py hides everything the backend spawns below it).
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    // First-run dependency install goes through whatever index uv is configured
    // to use (PyPI by default). We intentionally do NOT hardcode a regional
    // mirror here — install-source choice (e.g. a domestic mirror for mainland
    // networks) is left to the user's own uv config / UV_DEFAULT_INDEX env, and
    // agents are reminded of this in the server-side system prompt.

    // Pipe backend stdout+stderr to a log file so first-run failures are
    // diagnosable (the old code discarded them with Stdio::null()).
    let log_path = home.join("backend.log");
    let (out, err): (Stdio, Stdio) = match fs::File::create(&log_path) {
        Ok(f) => {
            let err = f.try_clone().ok().map(Stdio::from).unwrap_or_else(Stdio::null);
            (Stdio::from(f), err)
        }
        Err(_) => (Stdio::null(), Stdio::null()),
    };

    cmd.env("POLYNOIA_INSTANCE_MODE", "desktop_embedded")
        .env("POLYNOIA_INSTANCE_ID", instance_id)
        .env("POLYNOIA_HOST", "127.0.0.1")
        .env("POLYNOIA_PORT", port.to_string())
        .env("POLYNOIA_POLYNOIA_HOME", &home)
        .env(
            "POLYNOIA_DB_URL",
            format!("sqlite+aiosqlite:///{}", home.join("polynoia.db").display()),
        )
        .env("POLYNOIA_FILES_DIR", home.join("files"))
        .env("POLYNOIA_SANDBOX_ROOT", home.join("sandbox"))
        .env("UV_PROJECT_ENVIRONMENT", home.join(".venv"))
        .env("UV_CACHE_DIR", home.join("uv-cache"))
        .stdin(Stdio::null())
        .stdout(out)
        .stderr(err);
    Ok(cmd)
}

fn uvicorn_command(server_dir: PathBuf, port: u16, uv: &OsStr) -> Command {
    let mut c = Command::new(uv);
    c.current_dir(&server_dir)
        .arg("run")
        .arg("uvicorn")
        .arg("polynoia.main:app")
        .arg("--app-dir")
        .arg(&server_dir)
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string());
    c
}

// Prefer a uv we ship inside the bundle so the target machine needs nothing on
// PATH; fall back to a uv on PATH (dev machines / power users).
fn uv_program(resource_dir: Option<&Path>) -> OsString {
    let names: [&str; 2] = if cfg!(windows) { ["uv.exe", "uv"] } else { ["uv", "uv.exe"] };
    let mut bases: Vec<PathBuf> = Vec::new();
    if let Some(rd) = resource_dir {
        bases.push(rd.join("resources/bin"));
        bases.push(rd.join("bin"));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            bases.push(parent.join("resources/bin"));
            bases.push(parent.join("bin"));
            bases.push(parent.join("../Resources/bin"));
        }
    }
    for base in bases {
        for n in names {
            let p = base.join(n);
            if p.exists() {
                return p.into_os_string();
            }
        }
    }
    OsString::from("uv")
}

fn is_server_dir(p: &Path) -> bool {
    p.join("polynoia/main.py").exists() && p.join("pyproject.toml").exists()
}

// Resolve the bundled Python source dir across every layout: Tauri resource dir
// (Windows: <resource_dir>\resources\server), macOS .app Resources, and the dev
// tree. The old code only checked the macOS paths, so a packaged Windows app on
// a machine without the dev checkout found nothing.
fn find_server_dir(resource_dir: Option<&Path>) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(rd) = resource_dir {
        candidates.push(rd.join("resources/server"));
        candidates.push(rd.join("server"));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(p) = exe.parent() {
            candidates.push(p.join("resources/server"));
            candidates.push(p.join("../Resources/server"));
            candidates.push(p.join("../Resources/resources/server"));
            candidates.push(p.join("../../Resources/server"));
            candidates.push(p.join("../../Resources/resources/server"));
        }
    }
    if let Some(found) = candidates.into_iter().find(|p| is_server_dir(p)) {
        return Some(found);
    }
    find_dev_server_dir()
}

// Optional frozen single-file backend (PyInstaller-style). Not produced today,
// but kept so a future fully-offline build can drop a binary into resources/bin.
fn find_bundled_server(resource_dir: Option<&Path>) -> Option<PathBuf> {
    let mut dirs: Vec<PathBuf> = Vec::new();
    if let Some(rd) = resource_dir {
        dirs.push(rd.join("resources/bin"));
        dirs.push(rd.join("bin"));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            dirs.push(parent.to_path_buf());
            dirs.push(parent.join("resources/bin"));
            dirs.push(parent.join("../Resources"));
        }
    }
    for dir in dirs {
        for name in [
            "polynoia-server",
            "polynoia-server.exe",
            "polynoia-server-macos",
            "polynoia-server-aarch64-apple-darwin",
        ] {
            let p = dir.join(name);
            if p.exists() {
                return Some(p);
            }
        }
    }
    None
}

fn find_dev_server_dir() -> Option<PathBuf> {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repo = manifest.parent()?.parent()?.parent()?;
    let server = repo.join("apps/server");
    if server.join("polynoia/main.py").exists() {
        Some(server)
    } else {
        None
    }
}

fn wait_for_health(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if probe_health(port) {
            return true;
        }
        thread::sleep(Duration::from_millis(300));
    }
    false
}

fn probe_health(port: u16) -> bool {
    let Ok(mut stream) = TcpStream::connect(("127.0.0.1", port)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(700)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(700)));
    if stream
        .write_all(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut buf = String::new();
    stream.read_to_string(&mut buf).is_ok() && buf.contains("200 OK")
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

// Bring the main window back from the tray / minimized state and focus it.
fn show_main<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            desktop_backend_status,
            start_desktop_backend
        ])
        // Close-to-tray: clicking the window's X hides it instead of quitting, so
        // the embedded backend (and any in-flight agent turns) keep running. The
        // tray menu's "退出" is the real quit. Minimize works as usual via the
        // standard title-bar button.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        // Inject `window.__POLYNOIA_PLATFORM__ = "desktop"` so the web code
        // can detect the host without UA sniffing.
        .setup(|app| {
            // System tray: restore on left-click, with a menu to show / really quit.
            let show_item =
                MenuItem::with_id(app, "show", "显示 Polynoia", true, None::<&str>)?;
            let quit_item =
                MenuItem::with_id(app, "quit", "退出 Polynoia", true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&show_item, &quit_item])?;
            let _tray = TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().cloned().expect("default window icon"))
                .tooltip("Polynoia")
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;
            let data_dir = app
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| std::env::temp_dir().join("polynoia-desktop"));
            let resource_dir = app.path().resource_dir().ok();
            let manager = DesktopBackend {
                data_dir,
                resource_dir,
                inner: Mutex::new(BackendInner {
                    info: BackendInfo {
                        mode: "desktop_embedded".into(),
                        status: "starting".into(),
                        url: None,
                        pid: None,
                        message: "正在准备桌面内置后端".into(),
                    },
                    child: None,
                }),
            };
            let info = ensure_embedded_backend(&manager);
            app.manage(manager);
            if let Some(window) = app.get_webview_window("main") {
                let js = format!(
                    "window.__POLYNOIA_PLATFORM__='desktop';window.__POLYNOIA_DESKTOP_BACKEND__={};",
                    serde_json::to_string(&info).unwrap_or_else(|_| "null".into())
                );
                let _ = window.eval(&js);
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
