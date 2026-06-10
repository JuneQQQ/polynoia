// Prevent additional console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::{
    fs,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{Manager, State};

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

fn ensure_embedded_backend(state: &DesktopBackend) -> BackendInfo {
    let mut inner = state.inner.lock().expect("backend mutex");
    if let Some(child) = inner.child.as_mut() {
        match child.try_wait() {
            Ok(None) => return inner.info.clone(),
            Ok(Some(status)) => {
                inner.info = BackendInfo {
                    mode: "desktop_embedded".into(),
                    status: "stopped".into(),
                    url: None,
                    pid: None,
                    message: format!("内置后端已退出: {status}"),
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
    let mut cmd = match backend_command(port, &state.data_dir) {
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
        message: "内置后端正在启动".into(),
    };
    inner.child = Some(child);
    drop(inner);

    let ready = wait_for_health(port, Duration::from_secs(18));
    let mut inner = state.inner.lock().expect("backend mutex");
    if ready {
        inner.info = BackendInfo {
            mode: "desktop_embedded".into(),
            status: "running".into(),
            url: Some(url),
            pid: Some(pid),
            message: "桌面内置后端已启动".into(),
        };
    } else {
        if let Some(child) = inner.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        inner.child = None;
        inner.info = BackendInfo {
            mode: "desktop_embedded".into(),
            status: "error".into(),
            url: None,
            pid: None,
            message: "内置后端启动超时,请改用本机共享后端或自定义后端".into(),
        };
    }
    inner.info.clone()
}

fn reserve_local_port() -> Result<u16, String> {
    TcpListener::bind(("127.0.0.1", 0))
        .map_err(|e| e.to_string())
        .and_then(|listener| listener.local_addr().map_err(|e| e.to_string()).map(|a| a.port()))
}

fn backend_command(port: u16, data_dir: &Path) -> Result<Command, String> {
    fs::create_dir_all(data_dir).map_err(|e| format!("无法创建桌面数据目录: {e}"))?;
    let instance_id = format!("desktop-{}-{port}", now_ms());
    let home = data_dir.join("embedded");
    fs::create_dir_all(&home).map_err(|e| format!("无法创建内置后端数据目录: {e}"))?;

    let mut cmd = if let Ok(template) = std::env::var("POLYNOIA_DESKTOP_SERVER_CMD") {
        let rendered = template.replace("{port}", &port.to_string());
        let mut c = Command::new("sh");
        c.arg("-lc").arg(rendered);
        c
    } else if let Some(sidecar) = find_bundled_server() {
        let mut c = Command::new(sidecar);
        c.arg("--host").arg("127.0.0.1").arg("--port").arg(port.to_string());
        c
    } else if let Some(server_dir) = find_bundled_server_dir() {
        uvicorn_command(server_dir, port)
    } else if let Some(server_dir) = find_dev_server_dir() {
        uvicorn_command(server_dir, port)
    } else {
        return Err("未找到内置后端二进制,也未找到开发环境 apps/server。请切换到本机共享后端或自定义后端。".into());
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
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    Ok(cmd)
}

fn uvicorn_command(server_dir: PathBuf, port: u16) -> Command {
    let mut c = Command::new("uv");
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

fn find_bundled_server() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let mut dirs = Vec::new();
    if let Some(parent) = exe.parent() {
        dirs.push(parent.to_path_buf());
        dirs.push(parent.join("../Resources"));
        dirs.push(parent.join("../../Resources"));
    }
    for dir in dirs {
        for name in ["polynoia-server", "polynoia-server-macos", "polynoia-server-aarch64-apple-darwin"] {
            let p = dir.join(name);
            if p.exists() {
                return Some(p);
            }
        }
    }
    None
}

fn find_bundled_server_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let mut dirs = Vec::new();
    if let Some(parent) = exe.parent() {
        dirs.push(parent.join("../Resources/server"));
        dirs.push(parent.join("../Resources/resources/server"));
        dirs.push(parent.join("../../Resources/server"));
        dirs.push(parent.join("../../Resources/resources/server"));
    }
    dirs.into_iter()
        .find(|p| p.join("polynoia/main.py").exists() && p.join("pyproject.toml").exists())
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
    let deadline = std::time::Instant::now() + timeout;
    while std::time::Instant::now() < deadline {
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

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            desktop_backend_status,
            start_desktop_backend
        ])
        // Inject `window.__POLYNOIA_PLATFORM__ = "desktop"` so the web code
        // can detect the host without UA sniffing.
        .setup(|app| {
            // Inject the platform tag in BOTH dev and release builds. This was
            // previously gated behind `#[cfg(debug_assertions)]`, so packaged
            // `.app`/`.dmg` builds never set `__POLYNOIA_PLATFORM__` and silently
            // depended on the `__TAURI_INTERNALS__` fallback in platform.ts —
            // contradicting the documented design (CLAUDE.md §6.3), which makes
            // this the *primary* desktop-detection signal.
            let data_dir = app
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| std::env::temp_dir().join("polynoia-desktop"));
            let manager = DesktopBackend {
                data_dir,
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
