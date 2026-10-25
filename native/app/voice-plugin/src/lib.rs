//! Platform voice input. Only iOS records audio in this application.

#[cfg(not(target_os = "ios"))]
use serde::Serialize;
use tauri::Runtime;
use tauri::plugin::{Builder, TauriPlugin};

#[cfg(target_os = "ios")]
tauri::ios_plugin_binding!(init_plugin_workspace_voice);

#[cfg(not(target_os = "ios"))]
#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum Capability {
    #[cfg(windows)]
    SystemDictation,
    #[cfg(not(windows))]
    Unavailable { reason: &'static str },
}

#[cfg(not(target_os = "ios"))]
#[derive(Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum VoiceEvent {
    #[cfg(windows)]
    SystemDictation { session_id: String },
}

#[cfg(not(target_os = "ios"))]
#[tauri::command]
fn capability() -> Capability {
    #[cfg(windows)]
    return Capability::SystemDictation;
    #[cfg(not(windows))]
    Capability::Unavailable {
        reason: "Native speech is available on iPhone; use system dictation or type on this platform.",
    }
}

#[cfg(not(target_os = "ios"))]
#[tauri::command(rename_all = "snake_case")]
fn start(
    session_id: String,
    on_event: tauri::ipc::Channel<VoiceEvent>,
    locale: Option<String>,
) -> Result<(), String> {
    if session_id.is_empty() || session_id.len() > 128 {
        return Err("The voice session identity is invalid.".to_owned());
    }
    let _ = locale;
    #[cfg(windows)]
    {
        launch_system_dictation()?;
        on_event
            .send(VoiceEvent::SystemDictation { session_id })
            .map_err(|error| error.to_string())
    }
    #[cfg(not(windows))]
    {
        let _ = on_event;
        Err("Native speech is unavailable on this platform.".to_owned())
    }
}

#[cfg(windows)]
fn launch_system_dictation() -> Result<(), String> {
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        GetAsyncKeyState, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP, SendInput,
        VK_CONTROL, VK_H, VK_LWIN, VK_MENU, VK_RWIN, VK_SHIFT,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetWindowThreadProcessId,
    };
    let mut foreground_process = 0;
    // Reading the current foreground owner neither changes focus nor borrows
    // another application's state. Refuse a delayed click after focus changed.
    unsafe { GetWindowThreadProcessId(GetForegroundWindow(), &mut foreground_process) };
    if foreground_process != std::process::id() {
        return Err("Return to the workspace command field before starting dictation.".to_owned());
    }
    for code in [VK_LWIN, VK_RWIN, VK_H, VK_CONTROL, VK_MENU, VK_SHIFT] {
        // Do not release or combine with a physical modifier held by the user.
        if unsafe { GetAsyncKeyState(i32::from(code)) } < 0 {
            return Err("Release the held shortcut keys before starting dictation.".to_owned());
        }
    }
    let key = |code, flags| INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: code,
                wScan: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    };
    let inputs = [
        key(VK_LWIN, 0),
        key(VK_H, 0),
        key(VK_H, KEYEVENTF_KEYUP),
        key(VK_LWIN, KEYEVENTF_KEYUP),
    ];
    // SendInput copies this valid fixed array during the call. Its result is
    // only an input-delivery observation, never proof of recognized speech.
    let sent = unsafe {
        SendInput(
            inputs.len() as u32,
            inputs.as_ptr(),
            size_of::<INPUT>() as i32,
        )
    };
    if sent != inputs.len() as u32 {
        if sent > 0 {
            let release = [key(VK_H, KEYEVENTF_KEYUP), key(VK_LWIN, KEYEVENTF_KEYUP)];
            // One bounded cleanup attempt for a partially inserted shortcut.
            // This does not establish that Windows accepted either release.
            unsafe { SendInput(2, release.as_ptr(), size_of::<INPUT>() as i32) };
        }
        return Err("Windows did not accept the complete dictation shortcut; key release was attempted if needed.".to_owned());
    }
    Ok(())
}

#[cfg(not(target_os = "ios"))]
#[tauri::command]
fn stop() -> Result<(), String> {
    Err("System dictation owns its microphone; stop it using the Windows voice control.".to_owned())
}

#[cfg(not(target_os = "ios"))]
#[tauri::command]
fn cancel() -> Result<(), String> {
    stop()
}

pub fn init<R: Runtime>() -> TauriPlugin<R> {
    let builder = Builder::new("workspace-voice");
    #[cfg(target_os = "ios")]
    let builder = builder.setup(|_app, api| {
        api.register_ios_plugin(init_plugin_workspace_voice)?;
        Ok(())
    });
    #[cfg(not(target_os = "ios"))]
    let builder = builder.invoke_handler(tauri::generate_handler![capability, start, stop, cancel]);
    builder.build()
}
