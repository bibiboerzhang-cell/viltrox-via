# V-KPI P2.21 Live Crawler Samples

日期: 2026-05-10

## 范围

本轮只做 TikTok / Bilibili / Xiaohongshu 单账号真实小样本校准。

目标:

- 确认 3 个 crawler 已注册。
- 确认本地 provider key 能被单次命令加载。
- 确认真实 provider 请求返回后，`calculate_kpis()` 至少能映射出关键 KPI。
- 不打开平台预算 gate，不批量抓取，不写生产账号表。

## 环境

- Runtime env: `scripts/runtime_env.sh`
- Database: local `127.0.0.1:54329/viltrox2`
- Provider key: 从 `.env` 单次导出 `APIFY_TOKEN`，不打印 token。
- Backup before QA: `/Users/bibiboer/Documents/V-KPI-backups/before-p2.21-live-crawler-samples-20260510-213715.tar.gz`

注意: `runtime_env.sh` 不导出 provider key，这是安全设计；live QA 命令需要单次导出 `APIFY_TOKEN`，同时保留 `DATABASE_URL=54329/viltrox2`。

## Readiness

离线 readiness 通过:

- 支持平台: `youtube`, `instagram`, `tiktok`, `xiaohongshu`, `bilibili`, `x`, `twitch`, `reddit`, `facebook`
- 缺失注册: 0
- TikTok / Bilibili / Xiaohongshu: registered = true, configured = true
- 三个平台 `monthly_budget_usd=0.0`，live gate 仍为 closed

## Live 样本结果

所有命令均使用 `--ignore-gates`，只绕过本次人工校准的预算/开关 gate；未修改系统设置。

### Bilibili

命令摘要:

```bash
.venv/bin/python scripts/smoke_vkpi_crawler_live_mapping_guard.py \
  --live --platform bilibili --handle 373471445 --max-posts 1 --ignore-gates
```

结果:

- provider_status: `ok`
- sync_status: `synced`
- items: `2`
- mapping_status: `mapped`
- mapped_kpis: `followers=79241`, `posts=1052`

### Xiaohongshu

命令摘要:

```bash
.venv/bin/python scripts/smoke_vkpi_crawler_live_mapping_guard.py \
  --live --platform xiaohongshu --handle 60346fc0000000000101c9be --max-posts 1 --ignore-gates
```

结果:

- provider_status: `ok`
- sync_status: `synced`
- items: `1`
- mapping_status: `mapped`
- mapped_kpis: `followers=0`, `posts=0`, `likes=0`

说明: 这些 0 是真实 provider 返回后的映射结果；本轮确认了真实 0 不会被当成 missing/falsy 丢失。

### TikTok

命令摘要:

```bash
.venv/bin/python scripts/smoke_vkpi_crawler_live_mapping_guard.py \
  --live --platform tiktok --handle viltrox --max-posts 1 --ignore-gates
```

结果:

- provider_status: `ok`
- sync_status: `synced`
- items: `1`
- mapping_status: `mapped`
- mapped_kpis: `followers=5`, `posts=0`

## 风险控制

本轮没有做以下事项:

- 没有开启平台 `monthly_budget_usd`。
- 没有开启批量 crawler 调度。
- 没有写 `vkpi_industry_accounts` 生产账号。
- 没有输出 provider token。
- 没有保存完整 raw payload 到文档。

## 结论

P2.21 通过。TikTok / Bilibili / Xiaohongshu 三个平台的 live crawler 链路均可执行，且 KPI 映射没有完全断层。

下一步应做 P2.22 轻量 UX 修复，优先处理 KOL drawer 与 Project detail drawer 叠层；不要继续 D 系列无边界拆分。
