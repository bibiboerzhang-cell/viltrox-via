# V-KPI 本地算力 Runner — 桌面端(Tauri MVP)

把已跑通的 CLI runner(`scripts/vkpi_local_runner.py`)包成一个桌面 App:
非技术员工双击打开,填「服务器地址 + 邮箱 + 密码」,点「开始」,本机就开始
把服务器下放的**四类安全任务**领回来跑,不占用服务器算力。

- 交互壳:Tauri v2(Rust,`src-tauri/`)+ vanilla HTML/JS 前端(`index.html` / `main.js`)。
- runner 集成:**sidecar 简单路线** —— Rust 后端用系统 `python3` 直接跑
  `scripts/vkpi_local_runner.py`,把它的 stdout/stderr 逐行以事件流回 UI。
- 安全边界不变:壳自身不碰业务决策、不持有长期 key;只领
  `video_precheck / metadata_extract / download_frames / comment_clean`,
  只拿短期任务 token(全由 CLI runner + 服务端契约保证)。

> MVP 骨架:目录、配置、README 就位,**尚未编译**(开发机无 Rust/Tauri 工具链)。
> 下方给全 dev/build 命令与前置依赖。

## 目录结构

```
desktop/
├── README.md            # 本文
├── package.json         # 可选:npm 版 @tauri-apps/cli 入口
├── index.html           # 前端 UI(frontendDist 指向本目录)
├── main.js              # 前端逻辑:表单 → invoke start/stop → 监听日志事件
└── src-tauri/
    ├── tauri.conf.json  # 应用名/窗口/最小权限/CSP
    ├── Cargo.toml       # Rust 依赖(tauri v2 + serde)
    ├── build.rs         # tauri_build::build()
    ├── capabilities/
    │   └── default.json # 最小权限:仅本地 IPC + 事件监听,无网络/FS/shell 插件
    ├── icons/
    │   └── README.md    # 打包前用 `cargo tauri icon` 生成图标
    └── src/
        └── main.rs      # Rust 外壳:start_runner / stop_runner / runner_running
```

## 前置依赖(仅开发/打包机需要)

1. **Rust 工具链**:https://rustup.rs → `rustup default stable`(需 rustc ≥ 1.77)。
2. **系统 WebView**:
   - macOS:自带 WKWebView,无需额外装。
   - Windows:WebView2 Runtime(Win11 自带;Win10 需装)。
   - Linux:`libwebkit2gtk-4.1-dev` 等(见 Tauri 官方 prerequisites)。
3. **Tauri CLI**(任选其一):
   - Cargo 版:`cargo install tauri-cli --version "^2"` → 用 `cargo tauri ...`
   - npm 版:`npm install`(读 `package.json`)→ 用 `npx tauri ...`
4. **员工运行机**:装好 `python3`(runner 只用标准库),另按需装 `yt-dlp` /
   `ffmpeg`(缺了会把对应任务标 `skipped`,不影响 `comment_clean` 纯本地任务)。

## 开发运行

```bash
cd desktop
cargo tauri dev          # 或:npx tauri dev
```

窗口起来后:
1. 填**服务器地址**(如 `http://127.0.0.1:8102` 或线上 `https://kpi.example.com`)。
2. 填**邮箱 / 密码 / 设备名**。
3. 填 **runner 脚本绝对路径** = 本仓库的 `.../scripts/vkpi_local_runner.py`。
   - 「python 解释器」缺省 `python3`;若 runner 需要仓库虚拟环境,填 `.venv/bin/python`
     的绝对路径,并确保它能 import 到 runner 所需模块(runner 仅用标准库,一般 `python3` 即可)。
4. 「任务类型」留空 = 领全部安全类型;可填如 `comment_clean` 只领纯本地任务先冒烟。
5. 勾「只领一条…」= 传 `--once`,领一条跑完即退出,适合首次验证。
6. 点**开始**;日志区实时滚动 runner 的 `[runner] leased… / submitted…` 状态流。

## 打包各平台

```bash
cd desktop
cargo tauri icon path/to/logo.png    # 首次:生成 src-tauri/icons/ 全套图标
cargo tauri build                    # 产物在 src-tauri/target/release/bundle/
```

- macOS → `.app` / `.dmg`(`--target aarch64-apple-darwin` 或 `x86_64-apple-darwin`)。
- Windows → `.msi` / `.exe`(在 Windows 机上打)。
- Linux → `.AppImage` / `.deb`(在 Linux 机上打)。

Tauri 不做交叉编译:每个目标平台需在对应 OS 上执行 `cargo tauri build`。

## 员工怎么用(打包后)

1. 双击安装/打开 **V-KPI Runner**。
2. 填运维给的**服务器地址**和自己的**账号密码**,填 runner 脚本路径。
3. 点**开始**,让它常驻后台领活即可;要停就点**停止**或关窗口。
   - 密码只用于本机登录换短期任务 token,不写命令行、不写日志。
   - 本机只跑四类安全任务,拿不到也不需要任何长期 API key。

## PyInstaller 路线(可选,免装 python)

上面的简单路线要求员工机有 `python3`。若想让员工机**零依赖**,可把 runner 冻结成
单文件二进制,作为 Tauri **sidecar** 随 App 一起分发:

1. 在有 python 的机器上:
   ```bash
   pip install pyinstaller
   pyinstaller --onefile --name vkpi-runner scripts/vkpi_local_runner.py
   ```
   产物 `dist/vkpi-runner`(Windows 为 `vkpi-runner.exe`)。
2. 放到 `src-tauri/binaries/vkpi-runner-<target-triple>`(如
   `vkpi-runner-aarch64-apple-darwin`),在 `tauri.conf.json` 加
   `bundle.externalBin: ["binaries/vkpi-runner"]`。
3. 把 `src/main.rs` 里 `Command::new("python3").arg(runner_script)` 改成用
   `tauri_plugin_shell` 的 sidecar API 启动打包后的二进制(参数不变,`--base/--email/…`,
   密码仍走 `VKPI_RUNNER_PASSWORD` env)。
4. 每个平台各冻结一份二进制;`cargo tauri build` 会把对应 sidecar 打进安装包。

> MVP 先走系统 python3 路线验证端到端;PyInstaller/sidecar 是后续「员工机零依赖」升级。

## 设计约束

- **不碰仓库现有代码**:本目录纯新增,runner 脚本与后端契约一字未改。
- **最小权限**:`capabilities/default.json` 只给 `core:default`(本地 IPC + 事件),
  不开任何网络/文件系统/shell 插件权限;子进程由**受信的 Rust 后端**启动,前端只能
  通过固定的 `start_runner/stop_runner/runner_running` 三个命令间接触发。
- **密码不落痕**:只经环境变量 `VKPI_RUNNER_PASSWORD` 传给子进程,不进 argv/日志。
