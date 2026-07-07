# 应用图标(打包前生成)

`tauri.conf.json` 的 `bundle.icon` 引用了本目录下的 32x32.png / 128x128.png /
128x128@2x.png / icon.icns / icon.ico。仓库里未附二进制图标,`cargo tauri build`
前请用一张 ≥1024x1024 的方形 PNG(如 Viltrox logo)自动生成全套:

```bash
cd desktop
cargo tauri icon path/to/logo.png     # 或 npx @tauri-apps/cli icon path/to/logo.png
```

命令会把各尺寸图标写入本目录。`cargo tauri dev` 通常无需图标即可跑起窗口;
只有 `cargo tauri build` 打包安装包时才必须。
