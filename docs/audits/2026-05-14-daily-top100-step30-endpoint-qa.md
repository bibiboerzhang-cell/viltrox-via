# P4 Step30 - Daily Top100 Endpoint QA

日期: 2026-05-14  
范围: Daily Top100 真实 HTTP endpoint 与 service 口径一致性验证  
性质: 只读 QA, 不生成新 digest, 不写业务数据

## 本轮交付

- 新增 `scripts/smoke_vkpi_p4_30_daily_top100_endpoint_qa.py`
- 验证真实后端 endpoint:
  - `GET /api/admin/vkpi/analytics/daily-digest/status`
  - `GET /api/admin/vkpi/analytics/daily-digest/status?product_sku=...`
  - `GET /api/admin/vkpi/analytics/daily-digest?staff_id=...&limit=100`
- 对比 service:
  - `analytics.daily_staff_outreach_digest_status(...)`
  - `analytics.list_daily_staff_outreach_digest(...)`

## 当前真实状态

```json
{
  "active_staff_db": 2,
  "eligible_staff_count": 2,
  "ready_staff_count": 2,
  "candidate_source": "outreach_suggestions",
  "total_candidates": 96,
  "duplicate_suggestion_count": 0,
  "product_sku_checked": "kol_pool",
  "staff_digest_checked": 40,
  "staff_digest_items": 3
}
```

结论:

- 当前本地库只有 `2` 个 active staff, 不是旧截图里的 `11` 个员工口径。
- Daily Top100 候选源不为空, 当前来自 `outreach_suggestions`。
- Endpoint 与 service 在 `staff count / candidate source / duplicate count / item totals` 上一致。
- 当前 `0/11` 属于旧状态或旧页面缓存口径, 不应作为当前本地数据状态判断。

## 验证命令

```bash
./scripts/run_smoke.sh smoke_vkpi_p4_30_daily_top100_endpoint_qa.py
```

结果:

```text
PASS=1 / FAIL=0 / TOTAL=1
```

匹配 Daily Top100 测试组:

```bash
./scripts/run_smoke.sh \
  smoke_vkpi_daily_digest_kol_pool_bridge.py \
  smoke_vkpi_daily_digest_staff_scope.py \
  smoke_vkpi_daily_digest_unique_assignment.py \
  smoke_vkpi_daily_digest_responsible_import.py \
  smoke_vkpi_daily_top100_source_trigger.py \
  smoke_vkpi_p4_3_daily_top100_source_gate.py \
  smoke_vkpi_p3_11c_daily_top100_ui_contract.py \
  smoke_vkpi_p4_30_daily_top100_endpoint_qa.py
```

结果:

```text
PASS=8 / FAIL=0 / TOTAL=8
```

## 风险与后续

- 员工数量未来增加时, Top100 覆盖率会随真实 active staff 变化, 不能硬编码 `11`。
- 若要看到更多员工覆盖, 必须先完成账号/员工开通, 并确认 `staff.active=1`。
- 浏览器视觉 QA 本轮未强制导航 Chrome, 因当前 Chrome 正在用户 Feishu 页面；本轮以真实 HTTP endpoint + UI 静态合约为准。

## 备份

```text
/Users/bibiboer/Documents/V-KPI-backups/before-p4-step30-daily-top100-endpoint-browser-20260514-184227.tar.gz
```
