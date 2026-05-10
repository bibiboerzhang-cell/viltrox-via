# P2.29 Other Platform Single-Account Refresh QA

更新时间: 2026-05-10

## 范围

P2.29 只复测非 YouTube 平台的单账号刷新链路，不扩大到批量同步:

- Instagram / TikTok / Bilibili / Xiaohongshu disabled account refresh 不写 fake snapshot。
- 同一批平台用真实结构 raw fixture 走 `collect_account_snapshot(force_local=True)`，确认 snapshot + post 写入 seam 可用。
- 提供显式 opt-in 的单平台 live refresh 入口。
- 不修改 Settings 平台开关。
- 不修改预算。
- 不打印 provider key。

## 为什么单独做 P2.29

P2.27 已覆盖 YouTube 单账号刷新，但 P2.27 的 live seed 账号默认使用 YouTube profile URL。该假设对 Bilibili/Xiaohongshu 等平台不可靠。

P2.29 新增平台专用 profile URL:

- Instagram: `https://www.instagram.com/{handle}/`
- TikTok: `https://www.tiktok.com/@{handle}`
- Bilibili: `https://space.bilibili.com/{mid}`
- Xiaohongshu: `https://www.xiaohongshu.com/user/profile/{user_id}`

## 文件

- `scripts/smoke_vkpi_p2_29_other_platform_refresh.py`
  - 默认离线，不触发外部 API。
  - 对 4 个非 YouTube 平台逐个创建 disabled account。
  - 通过 refresh API 确认 disabled account 不写 fake snapshot。
  - 通过 raw fixture 走 collector 写入 seam。
  - `VKPI_P2_29_LIVE=1` 时可跑一个真实账号刷新。

## 默认验证

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing

PYTHONPATH=backend .venv/bin/python -m py_compile \
  scripts/smoke_vkpi_p2_29_other_platform_refresh.py

./scripts/run_smoke.sh smoke_vkpi_p2_29_other_platform_refresh.py
```

期望:

```text
VKPI_P2_29_OTHER_PLATFORM_REFRESH_SMOKE_OK
```

## 可选真实小样本

只有在明确要消耗一次 provider 请求时运行:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
source scripts/runtime_env.sh

VKPI_P2_29_LIVE=1 \
VKPI_P2_29_PLATFORM=instagram \
VKPI_P2_29_HANDLE='viltrox.cine' \
VKPI_P2_29_MAX_POSTS=3 \
PYTHONPATH=backend .venv/bin/python scripts/smoke_vkpi_p2_29_other_platform_refresh.py
```

说明:

- live 模式只跑一个平台一个账号。
- `force_local=True` 只用于人工小样本校准，不会改 Settings 预算。
- 输出只包含 provider status、sync status、posts_written 和 staff id，不包含 key。

## 2026-05-10 校验记录

- 默认离线 smoke: PASS。
- Instagram live 小样本: PASS, `VKPI_P2_29_PLATFORM=instagram`, `VKPI_P2_29_HANDLE='viltrox.cine'`, `posts_written=3`, `residue=0`。
- 全量 smoke: PASS, `77/77`。

## 边界

- 本轮不跑批量同步。
- 本轮不启用平台预算。
- 本轮不触发评论抓取。
- 本轮不继续 D 系列拆分。
