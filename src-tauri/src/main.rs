//! Tauri 桌面壳：启动 Python sidecar（FastAPI，127.0.0.1:17689）并打开窗口。
//!
//! 窗口直接加载 `http://127.0.0.1:17689/`（sidecar 托管前端静态文件 + API 同源），
//! 不使用 Tauri 内嵌资源协议：tauri.localhost 在系统代理 / TUN 环境下会被拦截
//! 导致 ERR_CONNECTION_REFUSED，而 127.0.0.1 回环直连不受代理影响。
//!
//! sidecar 定位顺序：
//!   1. 打包态：exe 同级 job-radar-sidecar.exe（PyInstaller 产物，随包分发）
//!   2. 开发态：项目根 .venv\\Scripts\\python.exe -m coach.sidecar.main
//! 窗口退出时杀掉 sidecar 子进程。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

const SIDECAR_PORT: u16 = 17689;
const PROJECT_ROOT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/..");

struct Sidecar(Mutex<Option<Child>>);

/// 找到 sidecar 可执行对象（程序路径 + 附加参数）。
/// Windows 产物是 job-radar-sidecar.exe，Linux/macOS 是 job-radar-sidecar（无扩展名）。
fn sidecar_name() -> &'static str {
    if cfg!(windows) {
        "job-radar-sidecar.exe"
    } else {
        "job-radar-sidecar"
    }
}

fn locate_sidecar() -> (PathBuf, Vec<String>) {
    let name = sidecar_name();
    // 打包态 1：exe 同目录（onedir 形态：job-radar-sidecar\job-radar-sidecar.exe）
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let onedir = dir.join("job-radar-sidecar").join(name);
            if onedir.exists() {
                return (onedir, Vec::new());
            }
            let bundled = dir.join(name);
            if bundled.exists() {
                return (bundled, Vec::new());
            }
            let res = dir.join("resources").join(name);
            if res.exists() {
                return (res, Vec::new());
            }
        }
    }
    // 开发态：项目 venv 的 python（Windows: Scripts/python.exe；Linux/macOS: bin/python）
    let (py_dir, py_name) = if cfg!(windows) {
        (Path::new(PROJECT_ROOT).join(".venv").join("Scripts"), "python.exe")
    } else {
        (Path::new(PROJECT_ROOT).join(".venv").join("bin"), "python")
    };
    let py = py_dir.join(py_name);
    (py, vec!["-m".into(), "coach.sidecar.main".into()])
}

fn spawn_sidecar() -> Child {
    let (prog, args) = locate_sidecar();
    let mut cmd = Command::new(&prog);
    cmd.args(&args)
        .current_dir(PROJECT_ROOT)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    cmd.spawn().expect("无法启动 sidecar 进程")
}

/// 轮询 sidecar 健康检查直到就绪（上限 ~40s）。返回是否就绪。
fn wait_sidecar_ready() -> bool {
    for _ in 0..80 {
        if let Ok(mut s) = TcpStream::connect(("127.0.0.1", SIDECAR_PORT)) {
            let _ = s.write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n");
            let mut buf = [0u8; 64];
            let _ = s.read(&mut buf);
            return true;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

fn kill_child(handle: &tauri::AppHandle) {
    let state = handle.state::<Sidecar>();
    let mut guard = state.0.lock().unwrap();
    if let Some(mut child) = guard.take() {
        let _ = child.kill();
    }
}

fn main() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let child = spawn_sidecar();
            app.manage(Sidecar(Mutex::new(Some(child))));
            let ready = wait_sidecar_ready();
            let url = format!("http://127.0.0.1:{SIDECAR_PORT}/");
            let window = WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(url.parse().expect("sidecar URL 非法")),
            )
            .title("校招雷达 · 学习规划师")
            .inner_size(1240.0, 820.0)
            .min_inner_size(960.0, 640.0)
            .center()
            .build()
            .expect("窗口创建失败");
            if !ready {
                let _ = window.eval("document.title = '校招雷达 · sidecar 未就绪'");
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Tauri 应用构建失败");

    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            kill_child(app_handle);
        }
    });
}
