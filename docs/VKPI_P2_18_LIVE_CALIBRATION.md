# V-KPI P2.18 Live Crawler Calibration

更新时间: 2026-05-10

## 范围

P2.18 只做真实 API 小样本校准，不开启批量抓取，不改平台预算，不写生产同步数据。

目标:

- 验证 crawler registry 中真实 provider 能返回数据。
- 验证 `calculate_kpis()` 能从真实 payload 映射出非空 KPI。
- 验证 live guard 不输出 provider token。
- 保持平台 crawl gate 默认关闭，避免误烧预算。

## 当前 readiness

`smoke_vkpi_crawler_live_mapping_guard.py` 默认模式已验证:

- 注册平台: `youtube`, `instagram`, `tiktok`, `xiaohongshu`, `bilibili`, `x`, `twitch`, `reddit`, `facebook`
- 缺失注册: 0
- 多数平台 provider 已配置，但 `monthly_budget_usd=0` 或 `crawl_enabled=false`，所以 live gate 正常关闭。
- `twitch` 当前显示 `not_configured`。

## Live 样本结果

本轮只跑 2 个样本，均使用 `--ignore-gates` 做人工单次校准；没有修改系统预算或平台开关。

### Instagram

命令:

```bash
source scripts/runtime_env.sh >/tmp/vkpi-runtime-env.log
.venv/bin/python scripts/smoke_vkpi_crawler_live_mapping_guard.py \
  --live --platform instagram --handle viltrox.cine --max-posts 1 --ignore-gates
```

结果摘要:

- provider_status: `ok`
- sync_status: `synced`
- items: `1`
- mapping_status: `mapped`
- mapped_kpis: `followers`, `posts`, `views_30d`, `likes`, `comments`

### YouTube

命令:

```bash
source scripts/runtime_env.sh >/tmp/vkpi-runtime-env.log
.venv/bin/python scripts/smoke_vkpi_crawler_live_mapping_guard.py \
  --live --platform youtube --handle Viltrox --max-posts 1 --ignore-gates
```

结果摘要:

- provider_status: `ok`
- sync_status: `synced`
- items: `1`
- mapping_status: `mapped`
- mapped_kpis: `followers`, `posts`, `views`

## 工程修复

本轮对 `scripts/smoke_vkpi_crawler_live_mapping_guard.py` 做了一个小修复:

- 退出前显式调用 `close_db_runtime()`。
- 目的: 消除 psycopg pool 在 Python 3.14 进程退出时的 `PythonFinalizationError` 噪音。
- 不改变 crawler 行为，不改变 provider gate，不改变 KPI 映射。

## 后续边界

下一轮如果继续真实抓取，应保持以下限制:

- 一次只跑 1 个平台。
- 一次只跑 1 个账号。
- `max_posts` 不超过 1-3。
- 不打印完整 token、完整 provider URL、完整原始 payload。
- 只有在 smoke 和映射稳定后，才考虑打开平台预算 gate。
