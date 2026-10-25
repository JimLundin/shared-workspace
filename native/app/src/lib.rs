//! Native lifecycle and the local-only bridge to the shared workspace owner.

use std::io::Write;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use tauri::{Manager, RunEvent, State, WebviewWindowBuilder};
use tokio::sync::watch;
use workspace_core::{
    Command, CommandReceipt, Host, HostSnapshot, NetworkMode, PairingInvite, SyncReport,
};

struct NativeHost(Arc<Host>);

struct Lifecycle {
    foreground: watch::Sender<bool>,
    closing: Arc<AtomicBool>,
}

#[derive(serde::Serialize)]
struct HostCapabilities {
    platform: &'static str,
    pairing_camera: bool,
}

#[tauri::command]
fn host_capabilities() -> HostCapabilities {
    HostCapabilities {
        platform: std::env::consts::OS,
        pairing_camera: cfg!(target_os = "ios"),
    }
}

#[tauri::command]
async fn host_snapshot(host: State<'_, NativeHost>) -> Result<HostSnapshot, String> {
    host.0.snapshot().await.map_err(|error| error.to_string())
}

#[tauri::command]
async fn host_command(
    host: State<'_, NativeHost>,
    command: Command,
) -> Result<CommandReceipt, String> {
    host.0
        .command(command)
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command(rename_all = "snake_case")]
async fn host_invite(
    host: State<'_, NativeHost>,
    workspace_ids: Vec<String>,
    ttl_seconds: u32,
) -> Result<PairingInvite, String> {
    host.0
        .create_invite(workspace_ids, ttl_seconds)
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn host_join(host: State<'_, NativeHost>, ticket: String) -> Result<HostSnapshot, String> {
    host.0.join(ticket).await.map_err(|error| error.to_string())
}

#[tauri::command]
async fn host_sync(host: State<'_, NativeHost>) -> Result<SyncReport, String> {
    host.0.sync().await.map_err(|error| error.to_string())
}

#[tauri::command]
async fn host_set_foreground(host: State<'_, NativeHost>, foreground: bool) -> Result<(), String> {
    host.0
        .set_foreground(foreground)
        .await
        .map_err(|error| error.to_string())
}

fn device_name() -> String {
    #[cfg(target_os = "ios")]
    return "iPhone".to_owned();

    #[cfg(not(target_os = "ios"))]
    std::env::var("COMPUTERNAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .unwrap_or_else(|_| "Desktop".to_owned())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()
        .expect("The native workspace runtime could not start");
    tauri::async_runtime::set(runtime.handle().clone());
    let builder = tauri::Builder::default();
    #[cfg(target_os = "ios")]
    let builder = builder.plugin(tauri_plugin_barcode_scanner::init());
    let app = builder
        .plugin(tauri_plugin_workspace_voice::init())
        .invoke_handler(tauri::generate_handler![
            host_capabilities,
            host_snapshot,
            host_command,
            host_invite,
            host_join,
            host_sync,
            host_set_foreground,
        ])
        .setup(|app| {
            // The owner must finish opening its durable store before the UI can
            // receive a successful snapshot or command receipt.
            let directory = app.path().app_local_data_dir()?;
            std::fs::create_dir_all(&directory)?;
            let host = tauri::async_runtime::block_on(Host::open(
                &directory,
                device_name(),
                NetworkMode::Default,
            ))?;
            // Only the process holding the host directory lock may replace
            // its readiness receipt. A second launch must preserve the live
            // owner's marker when it fails to acquire that ownership.
            let receipt = directory.join("native-host-ready.json");
            if let Err(error) = std::fs::remove_file(&receipt)
                && error.kind() != std::io::ErrorKind::NotFound
            {
                return Err(error.into());
            }
            let snapshot = tauri::async_runtime::block_on(host.snapshot())?;
            let ready = serde_json::json!({
                "schema_version": 1,
                "state": "ready",
                "device_id": snapshot.data.device_id,
                "pid": std::process::id(),
                "started_at_ms": SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis(),
                "app_version": env!("CARGO_PKG_VERSION"),
            });
            let pending_receipt = receipt.with_extension("tmp");
            let mut file = std::fs::File::create(&pending_receipt)?;
            file.write_all(&serde_json::to_vec(&ready)?)?;
            file.sync_all()?;
            drop(file);
            std::fs::rename(pending_receipt, receipt)?;
            let (foreground, mut observations) = watch::channel(true);
            let owner = Arc::clone(&host);
            let closing = Arc::new(AtomicBool::new(false));
            let is_closing = Arc::clone(&closing);
            tauri::async_runtime::spawn(async move {
                while observations.changed().await.is_ok() {
                    if is_closing.load(Ordering::Acquire) {
                        break;
                    }
                    let active = *observations.borrow_and_update();
                    // A later foreground observation remains queued while a
                    // prior suspend finishes; callbacks cannot reorder effects.
                    if let Err(error) = owner.set_foreground(active).await {
                        eprintln!("workspace lifecycle update failed: {error}");
                    }
                }
            });
            app.manage(NativeHost(host));
            app.manage(Lifecycle {
                foreground,
                closing,
            });
            WebviewWindowBuilder::from_config(app, &app.config().app.windows[0])?
                .on_navigation(|url| {
                    (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
                        || (matches!(url.scheme(), "http" | "https")
                            && url.host_str() == Some("tauri.localhost"))
                })
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("The native workspace host could not start");

    app.run(|app, event| match event {
        RunEvent::ExitRequested { api, .. } => {
            let lifecycle = app.state::<Lifecycle>();
            if !lifecycle.closing.swap(true, Ordering::AcqRel) {
                api.prevent_exit();
                lifecycle.foreground.send_replace(false);
                let owner = Arc::clone(&app.state::<NativeHost>().0);
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    let code = if let Err(error) = owner.shutdown().await {
                        eprintln!("workspace shutdown failed: {error}");
                        1
                    } else {
                        0
                    };
                    app.exit(code);
                });
            }
        }
        #[cfg(mobile)]
        RunEvent::WindowEvent {
            event: tauri::WindowEvent::Suspended,
            ..
        } => {
            app.state::<Lifecycle>().foreground.send_replace(false);
        }
        #[cfg(mobile)]
        RunEvent::WindowEvent {
            event: tauri::WindowEvent::Resumed,
            ..
        } => {
            app.state::<Lifecycle>().foreground.send_replace(true);
        }
        RunEvent::Resumed => {
            let lifecycle = app.state::<Lifecycle>();
            if !lifecycle.closing.load(Ordering::Acquire) {
                lifecycle.foreground.send_replace(true);
            }
        }
        _ => {}
    });
}
