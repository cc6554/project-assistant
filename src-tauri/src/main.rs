//! Desktop launcher: use relocated release resources and per-user writable data.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

const SIDECAR_PORT: u16 = 17689;
#[cfg(debug_assertions)]
const PROJECT_ROOT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/..");
struct Sidecar(Mutex<Option<Child>>);

fn sidecar_name() -> &'static str {
    if cfg!(windows) { "job-radar-sidecar.exe" } else { "job-radar-sidecar" }
}

fn locate_sidecar() -> std::io::Result<(PathBuf, Vec<String>, PathBuf)> {
    let executable = std::env::current_exe()?;
    let directory = executable.parent().ok_or_else(|| std::io::Error::other("Executable has no directory"))?;
    for candidate in [directory.join("job-radar-sidecar").join(sidecar_name()), directory.join(sidecar_name())] {
        if candidate.is_file() {
            return Ok((candidate, Vec::new(), directory.to_path_buf()));
        }
    }
    #[cfg(debug_assertions)]
    {
        let root = Path::new(PROJECT_ROOT).canonicalize()?;
        let python = if cfg!(windows) { root.join(".venv/Scripts/python.exe") } else { root.join(".venv/bin/python") };
        if python.is_file() {
            return Ok((python, vec!["-m".into(), "coach.sidecar.main".into()], root));
        }
    }
    Err(std::io::Error::new(std::io::ErrorKind::NotFound, "Missing job-radar-sidecar; run the platform build script and launch the assembled release."))
}

fn spawn_sidecar(runtime: &Path, token: &str, instance: &str) -> std::io::Result<Child> {
    let (program, args, resources) = locate_sidecar()?;
    std::fs::create_dir_all(runtime)?;
    let mut command = Command::new(program);
    command.args(args).current_dir(&resources)
        .env("JOB_RADAR_HOME", runtime)
        .env("JOB_RADAR_RESOURCES", &resources)
        .env("JOB_RADAR_API_TOKEN", token)
        .env("JOB_RADAR_INSTANCE_ID", instance)
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    command.spawn()
}

fn wait_sidecar_ready(child: &mut Child, instance: &str) -> bool {
    let address: SocketAddr = ([127, 0, 0, 1], SIDECAR_PORT).into();
    for _ in 0..80 {
        if !matches!(child.try_wait(), Ok(None)) { return false; }
        if let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(250)) {
            let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
            let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
            // Do not disclose the authentication secret to an unrelated process on this port.
            let request = format!("GET /health HTTP/1.1\r\nHost: 127.0.0.1:{SIDECAR_PORT}\r\nConnection: close\r\n\r\n");
            if stream.write_all(request.as_bytes()).is_ok() {
                let mut response = String::new();
                if stream.take(8192).read_to_string(&mut response).is_ok() && response.starts_with("HTTP/1.1 200 ") {
                    if let Some(body) = response.split_once("\r\n\r\n").map(|(_, body)| body) {
                        if let Ok(value) = serde_json::from_str::<serde_json::Value>(body) {
                            if value.get("instance_id").and_then(|v| v.as_str()) == Some(instance) && matches!(child.try_wait(), Ok(None)) {
                                return true;
                            }
                        }
                    }
                }
            }
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

fn kill_child(handle: &tauri::AppHandle) {
    let state = handle.state::<Sidecar>();
    let mut guard = state.0.lock().unwrap();
    if let Some(mut child) = guard.take() { let _ = child.kill(); let _ = child.wait(); }
}

fn main() {
    let app = tauri::Builder::default().setup(|app| {
        let token = format!("{}{}", uuid::Uuid::new_v4().simple(), uuid::Uuid::new_v4().simple());
        let instance = uuid::Uuid::new_v4().simple().to_string();
        let runtime = match std::env::var_os("JOB_RADAR_HOME") {
            Some(path) => PathBuf::from(path),
            None => app.path().app_data_dir()?,
        };
        let mut child = spawn_sidecar(&runtime, &token, &instance)?;
        if !wait_sidecar_ready(&mut child, &instance) {
            let _ = child.kill(); let _ = child.wait();
            return Err(std::io::Error::other("Sidecar startup failed, timed out, or port 17689 belongs to another instance.").into());
        }
        app.manage(Sidecar(Mutex::new(Some(child))));
        let url = format!("http://127.0.0.1:{SIDECAR_PORT}/#token={token}");
        if let Err(error) = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse()?))
            .title("校招雷达 · 学习规划师").inner_size(1240.0, 820.0)
            .min_inner_size(960.0, 640.0).center().build() {
            kill_child(app.handle());
            return Err(error.into());
        }
        Ok(())
    }).build(tauri::generate_context!()).expect("Tauri 应用启动失败");
    app.run(|handle, event| {
        if let tauri::RunEvent::Exit = event { kill_child(handle); }
    });
}
