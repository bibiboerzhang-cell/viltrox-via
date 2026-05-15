# Daily Top100 Endpoint + Browser QA

Date: 2026-05-14
Round: P4 Step 18
Scope: Daily Top100 product-scope endpoint path and browser controls.

## Backup

- `/Users/bibiboer/Documents/V-KPI-backups/before-p4-step18-daily-top100-endpoint-browser-20260514-150528.tar.gz`

## Endpoint QA

Test account: `jianboz@viltrox.com`

Product scope: `AF-35-55-F1.8-EVO-FE-Z`

Checked endpoints:

- `GET /api/admin/vkpi/analytics/daily-digest/status`
- `GET /api/admin/vkpi/analytics/daily-digest/status?product_sku=AF-35-55-F1.8-EVO-FE-Z`
- `GET /api/admin/vkpi/analytics/suggestions?product_sku=AF-35-55-F1.8-EVO-FE-Z`
- `POST /api/admin/vkpi/analytics/daily-digest`
- `GET /api/admin/vkpi/analytics/daily-digest`

Observed product-scope result:

- Product suggestions: `6`
- Generated digest items: `5`
- Eligible staff: `2`
- Staff with generated list: `2`
- Candidate source: `outreach_suggestions`
- Duplicate suggestion count: `0`
- Owned assignment count: `0`
- Fallback assignment count: `5`

Conclusion: endpoint path is real and working. The remaining business note is that these suggestions are distributed by fallback assignment because no owner mapping exists for this product suggestion set.

## Browser QA

Page: `http://127.0.0.1:5173/#productBattle`

Controls verified:

- Product-scope input accepts `AF-35-55-F1.8-EVO-FE-Z`.
- `刷新口径` refreshes the visible scope and updates product-level candidate count from all products to scoped product.
- `查看分发细节` expands scheduling, source, assignment, and last-generated details.
- `按当前口径生成 Top100` calls the real generation path and updates visible counts.

Observed browser state after fix:

- Scope: `AF-35-55-F1.8-EVO-FE-Z`
- Candidate source: `产品监控候选`
- Product-level candidates: `6`
- Distributed: `5`
- Duplicate distribution: `0`
- Generated staff: `2 / 2`
- Ready staff: `2 / 2`

## Fix Applied

Problem found during browser QA:

- After clicking `按当前口径生成 Top100`, the success message showed `5` generated rows, but the status cards temporarily rendered `0 / 2`, `未开启`, and `未生成`.

Root cause:

- The UI treated the mutation response from `generateDailyOutreachDigest()` as the full status response. That response is valid for generation messaging but does not include every status-field used by the Daily Top100 status cards.

Change:

- `AnalyticsMonitorPanel.tsx` now calls `getDailyOutreachDigestStatus(apiToken, productSku)` immediately after generation and renders the refreshed status payload.
- `smoke_vkpi_p3_11c_daily_top100_ui_contract.py` now protects this behavior.

Files touched:

- `frontend/src/components/vkpi/pages/analytics/AnalyticsMonitorPanel.tsx`
- `scripts/smoke_vkpi_p3_11c_daily_top100_ui_contract.py`

## Validation

- `npm run build`: PASS
- `py_compile` for Daily Top100 smokes: PASS
- `smoke_vkpi_p3_11c_daily_top100_ui_contract.py`: PASS
- `smoke_vkpi_daily_digest_action_qa.py`: PASS
- `smoke_vkpi_p4_3_daily_top100_source_gate.py`: PASS
- `smoke_vkpi_daily_top100_source_trigger.py`: PASS
- Browser refresh/scope/generate QA: PASS

## Remaining Notes

- Daily Top100 source is not empty. It has real product-scope suggestions and real digest rows.
- `owned_assignment_count=0` is expected for this data set until suggestions carry responsible staff ownership.
- The frontend top bar still reports `BE checking` in the visible version badge while `/health` responds. This is a version-observability issue, not a Daily Top100 data-path blocker.
