# P3.11C Daily Top100 UI Scope Closure

Date: 2026-05-13
Repo: `/Users/bibiboer/Documents/V-KPI——marketing`
Backup: `/Users/bibiboer/Documents/V-KPI-backups/vkpi-before-p311c-daily-top100-ui-20260513-181322.tar.gz`

## Scope

Only Daily Top100 UI wording and product-scope controls were changed.
No backend assignment logic, permission scope, crawler, media, or Data Quality code was changed.

## Problem

The previous card used ambiguous ratio labels such as `有效员工 0/11`.
That mixed active staff, eligible staff, generated staff, and empty staff into one number, making a valid empty state look like a broken feature.

## Changes

- Added product-scope support to frontend Daily Top100 status and generate calls.
- Added an optional product SKU input for Daily Top100 status/generation.
- Replaced ambiguous `有效员工` label with explicit counts:
  - 活跃员工
  - 符合分发
  - 已生成清单
  - 有候选员工
  - 无候选员工
  - 已排除
- Added a source strip showing:
  - 当前口径
  - 候选来源
  - 产品级候选
  - 已分发
  - 重复分发
- Moved lower-frequency fields into a collapsed details section.

## Files Changed

- `frontend/src/services/vkpi.ui-api.ts`
- `frontend/src/components/vkpi/pages/analytics/AnalyticsMonitorPanel.tsx`
- `frontend/src/components/vkpi/VkpiDashboard.css`
- `scripts/smoke_vkpi_p3_11c_daily_top100_ui_contract.py`

## Verification

```bash
PYTHONPATH=backend .venv/bin/python scripts/smoke_vkpi_p3_11c_daily_top100_ui_contract.py
./scripts/run_smoke.sh smoke_vkpi_daily_top100_source_trigger.py
./scripts/run_smoke.sh smoke_vkpi_daily_digest_unique_assignment.py
./scripts/run_smoke.sh smoke_vkpi_daily_digest_staff_scope.py
cd frontend && npm run build
```

Results:

- `VKPI_P3_11C_DAILY_TOP100_UI_CONTRACT_OK`
- `smoke_vkpi_daily_top100_source_trigger.py` PASS
- `smoke_vkpi_daily_digest_unique_assignment.py` PASS
- `smoke_vkpi_daily_digest_staff_scope.py` PASS
- `npm run build` PASS

## Acceptance

P3.11C is complete when the card no longer presents `0/11` as a single ambiguous state and the user can refresh/generate Daily Top100 by all products or by a specific product SKU.

## Remaining P3.11 Work

- Browser QA once frontend dev server is available and not blocked by extension/client filtering.
- Optional: surface product picker from monitored products instead of free-text SKU.
