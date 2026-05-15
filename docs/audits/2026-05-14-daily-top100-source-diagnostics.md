# Daily Top100 Source Diagnostics

Date: 2026-05-14
Scope: P4 Step 17, Daily Top100 candidate source diagnostics only.

## Verdict

Daily Top100 is not currently blocked by a missing candidate source.

The current runtime database has:

- `1` enabled monitored product.
- `1` real product SKU with product-scoped outreach suggestions.
- `6` product-scoped suggestions for `AF-35-55-F1.8-EVO-FE-Z`.
- `5` assigned Daily Top100 items for that real product.
- `90` bridge or blank suggestions from KOL Pool compatibility rows.

This means the next risk is endpoint and browser QA, not rebuilding the source pipeline.

## Runtime Evidence

Command:

```bash
PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_daily_top100_source.py --json
```

Observed output summary:

```json
{
  "status": "ok",
  "blockers": [],
  "monitored_products_count": 1,
  "enabled_monitored_products_count": 1,
  "real_suggestion_skus": ["AF-35-55-F1.8-EVO-FE-Z"],
  "bridge_or_blank_suggestion_count": 90
}
```

Product-specific suggestion count:

```text
AF-35-55-F1.8-EVO-FE-Z / new / youtube = 6
```

Digest assignment snapshot:

```text
2026-05-13: staff_digest_count=2, item_count=5
assigned_by_product:
- kol_pool = 89
- AF-35-55-F1.8-EVO-FE-Z = 5
```

## Source Chain

Current source chain:

```text
monitored product
  -> product monitor / compare flow
  -> vkpi_outreach_suggestions.source_product_sku
  -> rank_uncontacted_suggestions()
  -> generate_daily_staff_outreach_digest()
  -> vkpi_staff_outreach_digests
  -> vkpi_staff_outreach_digest_items
```

Key API endpoints:

```text
GET  /api/admin/vkpi/analytics/daily-digest/status
GET  /api/admin/vkpi/analytics/daily-digest
POST /api/admin/vkpi/analytics/daily-digest/generate
GET  /api/admin/vkpi/analytics/suggestions
```

## Matched Validation

These tests match the scope of this diagnostics round:

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile \
  scripts/audit_vkpi_daily_top100_source.py \
  scripts/smoke_vkpi_p4_3_daily_top100_source_gate.py \
  scripts/smoke_vkpi_daily_top100_source_trigger.py \
  scripts/smoke_vkpi_daily_digest_unique_assignment.py \
  scripts/smoke_vkpi_daily_digest_staff_scope.py

./scripts/run_smoke.sh smoke_vkpi_p4_3_daily_top100_source_gate.py
./scripts/run_smoke.sh smoke_vkpi_daily_top100_source_trigger.py
./scripts/run_smoke.sh smoke_vkpi_daily_digest_unique_assignment.py
./scripts/run_smoke.sh smoke_vkpi_daily_digest_staff_scope.py
```

Result:

```text
py_compile: PASS
source gate smoke: PASS
source trigger smoke: PASS
unique assignment smoke: PASS
staff scope smoke: PASS
```

## Remaining Risks

1. Browser panel wording can still confuse users if it shows active staff, eligible staff, empty staff, and excluded staff as one combined number.
2. Product-specific candidates are present, but the UI still needs endpoint/browser QA to prove each visible button path works.
3. KOL Pool bridge suggestions still exist by design for compatibility; they should not be counted as product-specific suggestions.
4. If a future product has no monitor run, Daily Top100 may correctly show empty for that product until the monitor or manual trigger creates suggestions.

## Next Step

Proceed to Daily Top100 endpoint/browser QA:

- Verify status endpoint fields map to the UI labels.
- Verify manual generate creates or refreshes digest rows.
- Verify no duplicate suggestion assignment across staff.
- Verify employee-scoped view only shows the current staff digest.
- Verify empty-state wording separates "no candidates" from "not generated".

