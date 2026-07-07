// Prevents an extra console window on Windows in release builds. DO NOT REMOVE.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! V-KPI 本地算力 Worker 桌面外壳(Tauri v2)。
//!
//! 职责极窄:收前端填的 base/邮箱/密码/设备名,调「系统 python3 + scripts/
//! vkpi_local_runner.py」作为子进程(sidecar 简单路线),把它的 stdout/stderr
//! 逐行以事件回传给 UI。密码只经环境变量下发给子进程,绝不进 argv/日志。
//! 桌面壳自身不碰任何业务决策、不持有长期 token —— 全部安全边界仍由 CLI runner
//! 与服务端契约保证(只领四类安全任务、只拿短期任务 token)。

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};

use serde::Deserialize;
use tauri::{AppHandle, Emitter, State};

/// 子进程句柄的全局状态。用 Arc 以便读日志线程在进程结束时清理它。
struct RunnerState {
    child: Arc<Mutex<Option<Child>>>,
}

impl Default for RunnerState {
    fn default() -> Self {
        Self {
            child: Arc::new(Mutex::new(None)),
        }
    }
}

/// 前端 start 时下发的参数;字段名与 UI 表单一一对应。
#[derive(Deserialize)]
struct StartArgs {
    /// 服务器 base URL,如 https://kpi.example.com 或 http://127.0.0.1:8102
    base: String,
    /// 员工登录邮箱
    email: String,
    /// 登录密码 —— 只用于填子进程环境变量 VKPI_RUNNER_PASSWORD,不落 argv。
    password: String,
    /// 设备名(便于服务端识别这台机器)
    device_name: String,
    /// python 解释器路径,缺省 "python3"
    python_path: Option<String>,
    /// scripts/vkpi_local_runner.py 的绝对路径
    runner_script: String,
    /// 只领这些任务类型(逗号分隔);留空则交由 runner 领全部安全类型
    task_types: Option<String>,
    /// 领一条跑完即退出(冒烟用);缺省常驻轮询
    once: Option<bool>,
}

/// 启动 runner 子进程,并把它的输出以 `runner-log` 事件逐行推给前端。
#[tauri::command]
fn start_runner(app: AppHandle, state: State<'_, RunnerState>, args: StartArgs) -> Result<(), String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    if guard.is_some() {
        return Err("runner 已在运行,请先停止".into());
    }
    if args.runner_script.trim().is_empty() {
        return Err("runner 脚本路径为空:请填 scripts/vkpi_local_runner.py 的绝对路径".into());
    }

    let python = args
        .python_path
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("python3")
        .to_string();

    let mut cmd = Command::new(&python);
    cmd.arg(&args.runner_script)
        .arg("--base")
        .arg(args.base.trim())
        .arg("--email")
        .arg(args.email.trim())
        .arg("--device-name")
        .arg(args.device_name.trim());
    if let Some(tt) = args.task_types.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        cmd.arg("--task-types").arg(tt);
    }
    if args.once.unwrap_or(false) {
        cmd.arg("--once");
    }
    // 密码只经环境变量下发(与 CLI runner 契约一致),永不进命令行。
    cmd.env("VKPI_RUNNER_PASSWORD", &args.password);
    // 让 python 行缓冲即时刷出,日志才能实时流到 UI。
    cmd.env("PYTHONUNBUFFERED", "1");
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

    let mut child = cmd.spawn().map_err(|e| format!("启动子进程失败: {e}"))?;

    // stdout:逐行发 runner-log;EOF 视作进程结束,清理状态并发 runner-exit。
    if let Some(stdout) = child.stdout.take() {
        let app_out = app.clone();
        let child_arc = state.child.clone();
        std::thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                let _ = app_out.emit("runner-log", line);
            }
            // stdout 关闭 ≈ 进程退出:回收句柄并通知前端。
            if let Ok(mut g) = child_arc.lock() {
                if let Some(c) = g.as_mut() {
                    let _ = c.wait();
                }
                *g = None;
            }
            let _ = app_out.emit("runner-status", "stopped");
            let _ = app_out.emit("runner-exit", ());
        });
    }

    // stderr:同样逐行回传,带前缀便于 UI 区分。
    if let Some(stderr) = child.stderr.take() {
        let app_err = app.clone();
        std::thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                let _ = app_err.emit("runner-log", format!("[stderr] {line}"));
            }
        });
    }

    *guard = Some(child);
    let _ = app.emit("runner-status", "running");
    Ok(())
}

/// 停止正在运行的 runner 子进程。
#[tauri::command]
fn stop_runner(app: AppHandle, state: State<'_, RunnerState>) -> Result<(), String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    match guard.take() {
        Some(mut child) => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = app.emit("runner-status", "stopped");
            Ok(())
        }
        None => Err("runner 未在运行".into()),
    }
}

/// 查询当前是否有 runner 在跑(供 UI 恢复按钮态)。
#[tauri::command]
fn runner_running(state: State<'_, RunnerState>) -> bool {
    state.child.lock().map(|g| g.is_some()).unwrap_or(false)
}

fn main() {
    tauri::Builder::default()
        .manage(RunnerState::default())
        .invoke_handler(tauri::generate_handler![
            start_runner,
            stop_runner,
            runner_running
        ])
        .run(tauri::generate_context!())
        .expect("V-KPI Runner 桌面壳启动失败");
}
