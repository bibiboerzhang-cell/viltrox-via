# P2.24 Budget / Crawl Loop

日期: 2026-05-10

## 目标

把 Settings 里的平台抓取开关、平台月预算、全局 `crawl_total` 预算和 Apify 预算，真正接入 Data Analysis 的账号刷新闸门。用户在账号详情里可以看到“为什么开启抓取后仍未抓取”，避免误以为系统坏了。

## 后端变化

- `platform_crawl_settings.crawl_budget_gate(platform)`: 新增全局预算闸门。
- `industry_snapshot_collector.provider_gate()`: 账号刷新前除平台预算外，额外校验 `crawl_total` 和 Apify 预算。
- Apify 链路平台: Instagram / TikTok / Xiaohongshu / Bilibili / Facebook / Reddit / X。

闸门顺序:

1. 账号级 `crawl_enabled`
2. 平台级 `crawl_enabled`
3. 平台级 `monthly_budget_usd`
4. 全局 `crawl_total`
5. Apify 平台额外校验 `apify`
6. crawler 注册和 API 配置

## 前端变化

- Data Analysis 初始化时读取 `listPlatformCrawlSettings()` 和 `listBudgetSettings()`。
- `AccountDrawer` 新增“抓取闸门”面板。
- 面板展示账号抓取、平台开关、平台限制、平台月预算、全局 `crawl_total`、Apify 预算、API 状态。
- 第一条阻塞项会直接显示为“当前阻塞”。

## 验收

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
./scripts/run_smoke.sh smoke_vkpi_p2_24_budget_crawl_loop.py
npm run build
./scripts/run_smoke.sh --all
```

期望:

- `VKPI_P2_24_BUDGET_CRAWL_LOOP_SMOKE_OK`
- `npm run build` PASS
- 全量 smoke PASS

## 不包含

- 不打开任何平台批量抓取。
- 不消耗 Apify / YouTube API 额度。
- 不继续 D 系列拆分。
