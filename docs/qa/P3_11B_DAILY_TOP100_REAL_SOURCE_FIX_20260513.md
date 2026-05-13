# P3.11B Daily Top100 真实候选源修复

日期: 2026-05-13
范围: Daily Top100 候选源 / 产品监控触发链路

## 本轮目标

把 Daily Top100 从 `kol_pool` 桥接候选改为能产生真实产品级候选。

验收口径:
- `vkpi_monitored_products` 至少有 1 个真实启用产品。
- `analytics.monitor_product()` 能通过真实 Apify + Claude 路径生成产品级 `vkpi_outreach_suggestions`。
- Daily Top100 digest 能从该产品生成当天分发记录。
- 重复分配为 0。

## 开工前真实状态

`scripts/audit_vkpi_daily_top100_source.py --json` 显示:

- `status=blocked`
- blockers:
  - `no_monitored_products`
  - `suggestions_are_bridge_only`
  - `no_local_product_candidates`
- `vkpi_monitored_products=0`
- `vkpi_outreach_suggestions` 只有 `source_product_sku=kol_pool`

结论: 后端分配逻辑不是根因；缺的是真实产品监控入口和产品级候选生成。

## 执行动作

新增本地真实监控产品:

- product_sku: `AF-35-55-F1.8-EVO-FE-Z`
- product_name: `Viltrox AF 35mm / 55mm F1.8 EVO FE/Z`
- platforms: `youtube,instagram,tiktok`

执行真实 YouTube 监控:

- Apify actor: `streamers/youtube-scraper`
- max_videos: `10`
- Claude classification: `claude-sonnet-4-20250514`

真实返回:

- total_videos: `10`
- total_views: `664201`
- unique_creators: `9`
- avg_engagement_pct: `0.85`
- suggestions_created: `6`

## 修复代码

文件: `backend/app/services/vkpi/analytics.py`

问题:

`monitor_product()` 成功时 `metadata.provider_status=done`，旧代码只在 `provider_status` 为空时更新 `last_monitored_at/last_run_id`，导致成功监控反而不更新产品状态。

修复:

- 成功完成 monitor run 后总是更新 `vkpi_monitored_products.last_monitored_at` 和 `last_run_id`。

文件: `scripts/audit_vkpi_daily_top100_source.py`

修复:

- 脚本退出时关闭 DB runtime，消除 Python 3.14 `ConnectionPool.__del__` 退出噪音。

## 修复后真实状态

`audit_vkpi_daily_top100_source.py --json`:

- status: `ok`
- blockers: `[]`
- monitored_products_count: `1`
- enabled_monitored_products_count: `1`
- real_suggestion_skus: `["AF-35-55-F1.8-EVO-FE-Z"]`
- product-specific suggestions: `6`
- recent digest 2026-05-13:
  - staff_digest_count: `2`
  - item_count: `5`
  - assigned_by_product: `AF-35-55-F1.8-EVO-FE-Z = 5`

`daily_staff_outreach_digest_status(product_sku=...)`:

- status: `ok`
- active_staff_count: `11`
- eligible_staff_count: `2`
- generated_staff_count: `2`
- items_total: `5`
- duplicate_suggestion_count: `0`
- total_candidates: `6`
- uncontacted_count: `5`
- candidate_source: `outreach_suggestions`

说明:

- `0/11` 不是当前结果。当前语义是 11 个 active staff 中 2 个 eligible staff 参与该产品分发，9 个被规则排除。
- Daily Top100 已有真实产品级候选，后续需要 UI 明确展示 `active/eligible/excluded` 三个口径，避免误读。

## 验证

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile \
  backend/app/services/vkpi/analytics.py \
  scripts/audit_vkpi_daily_top100_source.py

./scripts/run_smoke.sh smoke_vkpi_daily_top100_source_trigger.py
./scripts/run_smoke.sh smoke_vkpi_daily_digest_unique_assignment.py
./scripts/run_smoke.sh smoke_vkpi_daily_digest_staff_scope.py
```

结果:

- py_compile PASS
- source trigger smoke PASS
- unique assignment smoke PASS
- staff scope smoke PASS

## 剩余风险

- 当前只真实验证了 YouTube 产品监控；Instagram/TikTok 仍需各自最小 live check。
- Daily Top100 UI 仍需展示更清晰的候选来源、员工覆盖口径和产品筛选结果。
- `eligible_staff_count=2` 是当前数据/权限状态，不代表所有 11 个员工都应该收到候选。
