// 觀瀾 — financial-analyst desktop shell
//
// Tauri 主入口. 启动时 spawn financial-analyst.exe sidecar (serve 模式),
// 然后加载 GuanLan UI 前端. 前端通过 SSE 调 http://127.0.0.1:9999.
//
// 关闭时杀掉 sidecar.

use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

// 全局 sidecar 进程句柄 (用 OnceLock 保证 cleanup 时能拿到)
static SIDECAR: std::sync::OnceLock<std::sync::Mutex<Option<CommandChild>>> =
    std::sync::OnceLock::new();

#[tauri::command]
fn backend_status() -> Result<String, String> {
    // 简单 health check — 前端可以调这个看 sidecar 起来没
    use std::time::Duration;
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .map_err(|e| e.to_string())?;
    match client.get("http://127.0.0.1:9999/health").send() {
        Ok(resp) if resp.status().is_success() => Ok("ready".into()),
        Ok(resp) => Err(format!("backend returned {}", resp.status())),
        Err(e) => Err(format!("backend unreachable: {}", e)),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // 启动 financial-analyst.exe sidecar (port 9999)
            let shell = app.shell();
            let sidecar_cmd = shell
                .sidecar("financial-analyst")
                .expect("sidecar 'financial-analyst' not bundled — check tauri.conf.json externalBin")
                .args(["serve", "--port", "9999"]);

            let (mut rx, child) = sidecar_cmd
                .spawn()
                .expect("failed to spawn financial-analyst sidecar");

            // 保存句柄供 cleanup
            SIDECAR
                .get_or_init(|| std::sync::Mutex::new(None))
                .lock()
                .unwrap()
                .replace(child);

            // 后台读 sidecar stdout/stderr 到 Tauri 日志
            tauri::async_runtime::spawn(async move {
                use tauri_plugin_shell::process::CommandEvent;
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                            let txt = String::from_utf8_lossy(&line);
                            eprintln!("[sidecar] {}", txt.trim_end());
                        }
                        CommandEvent::Terminated(payload) => {
                            eprintln!("[sidecar] terminated: {:?}", payload);
                            break;
                        }
                        _ => {}
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // 主窗口关闭 → 杀 sidecar
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if let Some(mutex) = SIDECAR.get() {
                    if let Ok(mut guard) = mutex.lock() {
                        if let Some(child) = guard.take() {
                            let _ = child.kill();
                            eprintln!("[sidecar] killed on window close");
                        }
                    }
                }
            }
            let _ = window;
        })
        .invoke_handler(tauri::generate_handler![backend_status])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn main() {
    run();
}
