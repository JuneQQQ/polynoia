// Polynoia desktop entry point.
//
// The Tauri shell wraps the existing @polynoia/web build (Vite). All business
// logic lives in the web app — this binary just hosts a native WebView window
// and (in the future) provides any OS-native bridges (file picker, notifications,
// menu bar, etc.) via #[tauri::command] handlers.

// Prevent additional console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        // Inject `window.__POLYNOIA_PLATFORM__ = "desktop"` so the web code
        // can detect the host without UA sniffing.
        .setup(|app| {
            // Inject the platform tag in BOTH dev and release builds. This was
            // previously gated behind `#[cfg(debug_assertions)]`, so packaged
            // `.app`/`.dmg` builds never set `__POLYNOIA_PLATFORM__` and silently
            // depended on the `__TAURI_INTERNALS__` fallback in platform.ts —
            // contradicting the documented design (CLAUDE.md §6.3), which makes
            // this the *primary* desktop-detection signal.
            use tauri::Manager;
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval("window.__POLYNOIA_PLATFORM__ = 'desktop';");
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
