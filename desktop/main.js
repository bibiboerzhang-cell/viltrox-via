// V-KPI Runner 前端逻辑(vanilla,无打包)。
// 通过 window.__TAURI__ 全局访问 Tauri v2 IPC 与事件;withGlobalTauri 已在
// tauri.conf.json 打开。所有安全边界仍由后端 Rust 命令 + CLI runner 保证。

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;

const $ = (id) => document.getElementById(id);
const startBtn = $("startBtn");
const stopBtn = $("stopBtn");
const statusEl = $("status");
const logEl = $("log");

let logInitialized = false;

function setStatus(kind, text) {
  statusEl.className = "badge " + kind;
  statusEl.textContent = text;
}

function setRunning(running) {
  startBtn.disabled = running;
  stopBtn.disabled = !running;
}

function appendLog(line, isErr) {
  if (!logInitialized) {
    logEl.textContent = "";
    logInitialized = true;
  }
  const row = document.createElement("div");
  if (isErr) row.className = "err";
  row.textContent = line;
  logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;
}

startBtn.addEventListener("click", async () => {
  const args = {
    base: $("base").value.trim(),
    email: $("email").value.trim(),
    password: $("password").value,
    device_name: $("device").value.trim() || "desktop-runner",
    python_path: $("python").value.trim() || null,
    runner_script: $("script").value.trim(),
    task_types: $("taskTypes").value.trim() || null,
    once: $("once").checked,
  };

  if (!args.base || !args.email || !args.password || !args.runner_script) {
    setStatus("error", "缺少必填项");
    appendLog("[ui] 请填写:服务器地址、邮箱、密码、runner 脚本路径。", true);
    return;
  }

  try {
    setStatus("running", "启动中…");
    setRunning(true);
    await invoke("start_runner", { args });
  } catch (e) {
    setStatus("error", "启动失败");
    appendLog("[ui] 启动失败: " + e, true);
    setRunning(false);
  }
});

stopBtn.addEventListener("click", async () => {
  try {
    await invoke("stop_runner");
  } catch (e) {
    appendLog("[ui] 停止失败: " + e, true);
  }
});

// 后端事件接线:日志流 / 状态 / 退出。
listen("runner-log", (event) => {
  const line = String(event.payload ?? "");
  appendLog(line, line.startsWith("[stderr]"));
});

listen("runner-status", (event) => {
  const s = String(event.payload ?? "");
  if (s === "running") {
    setStatus("running", "运行中");
    setRunning(true);
  } else if (s === "stopped") {
    setStatus("stopped", "已停止");
    setRunning(false);
  }
});

listen("runner-exit", () => {
  appendLog("[ui] runner 进程已退出。", false);
  setRunning(false);
});

// 启动时同步一次按钮态(热重载/重开窗口后)。
invoke("runner_running")
  .then((running) => {
    setRunning(!!running);
    if (running) setStatus("running", "运行中");
  })
  .catch(() => {});
