# P2.27 Single Account Refresh Guard

更新时间: 2026-05-10

## 范围

P2.27 只处理单账号刷新验证链路，不扩大到批量爬取:

- 确认账号未开启抓取时，刷新接口不会写假 snapshot。
- 确认真实刷新会走到的 snapshot + posts 写入路径可用。
- 修复 live mapping guard 直接运行时误读旧 `.env` 5432 的问题。
- 提供显式 opt-in 的小范围 live 验证入口。

## 文件

- `scripts/smoke_vkpi_p2_27_single_account_refresh.py`
  - 默认离线，不触发外部 API。
  - 通过 API 刷新 disabled account，确认不写 fake snapshot。
  - 通过 raw fixture 走 `collect_account_snapshot()`，确认 snapshot 和 post 可写。
  - `VKPI_P2_27_LIVE=1` 时可跑一个真实账号刷新。

- `scripts/smoke_vkpi_crawler_live_mapping_guard.py`
  - 直接运行时默认使用本地 `54329/viltrox2`，避免旧 `.env` 里的 5432 导致 PoolTimeout。

## 默认验证

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing

PYTHONPATH=backend .venv/bin/python -m py_compile \
  scripts/smoke_vkpi_p2_27_single_account_refresh.py \
  scripts/smoke_vkpi_crawler_live_mapping_guard.py

./scripts/run_smoke.sh smoke_vkpi_p2_27_single_account_refresh.py
./scripts/run_smoke.sh smoke_vkpi_crawler_live_mapping_guard.py
```

期望:

- `VKPI_P2_27_SINGLE_ACCOUNT_REFRESH_SMOKE_OK`
- `VKPI_CRAWLER_LIVE_MAPPING_GUARD_SMOKE_OK`

## 可选真实小样本

只有在明确要消耗一次 provider 请求时运行:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing

source scripts/runtime_env.sh
VKPI_P2_27_LIVE=1 \
VKPI_P2_27_PLATFORM=youtube \
VKPI_P2_27_HANDLE='@viltroxofficial' \
PYTHONPATH=backend .venv/bin/python scripts/smoke_vkpi_p2_27_single_account_refresh.py
```

说明:

- 不修改 Settings 里的平台开关或预算。
- 使用 `force_local=True` 绕过本地预算 gate，仅用于人工小样本校准。
- live 模式下 disabled-account 保护走服务内调用，不依赖当前 8102 后端进程的 JWT secret。
- 输出不包含 provider key。

2026-05-10 校验记录:

- `VKPI_P2_27_PLATFORM=youtube`
- `VKPI_P2_27_HANDLE='@viltroxofficial'`
- 结果: `provider_status=synced`, `sync_status=synced`, `posts_written=25`, `residue=0`

## 边界

- 不跑批量同步。
- 不自动开启平台预算。
- 不修改 `.env`。
- 不打印密钥。
