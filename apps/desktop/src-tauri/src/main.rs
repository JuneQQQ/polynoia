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
            #[cfg(debug_assertions)]
            {
                use tauri::Manager;
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.eval(
                        "window.__POLYNOIA_PLATFORM__ = 'desktop';",
                    );
                }
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
