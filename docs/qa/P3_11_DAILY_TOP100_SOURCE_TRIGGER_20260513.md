# P3.11 Daily Top100 Source / Trigger Audit

## Scope

This round validates the Daily Top100 upstream source instead of rebuilding assignment logic.
Existing assignment behavior was already covered by the staff-scope, owner-assignment, unique-assignment, responsible-import, and KOL Pool bridge smoke tests.

## Current Finding

Runtime audit before this round showed:

- `vkpi_outreach_suggestions.status=new`: 90 rows
- `vkpi_monitored_products`: 0 rows
- Current suggestions use `source_product_sku=kol_pool`

Conclusion: Daily Top100 is not empty, but its current data is a KOL Pool bridge source. The morning monitor cannot create fresh product-specific candidates because no monitored products are configured.

## Additional Data Check

Product catalog inspection found no reliable real product SKU:

- `vkpi_product_launches`: empty
- `vkpi_product_cost_catalog`: empty
- `vkpi_projects`: one ambiguous non-smoke row, `product_sku=35`, `project_name=35 1.2`

The audit script intentionally rejects bridge placeholders, smoke/test SKUs, and pure numeric SKUs. It will not auto-seed `kol_pool` or `35` as monitored products.

## Fix

Added:

- `scripts/audit_vkpi_daily_top100_source.py`
- `scripts/smoke_vkpi_daily_top100_source_trigger.py`

The audit script is read-only by default and refuses to treat `kol_pool` as a real product. It can safely seed monitored products only when explicitly called with `--apply` and either:

- `--product-sku` / `--product-name`, or
- `--from-catalog` when real product SKUs already exist in product/project tables.

The smoke proves:

1. Dry-run does not write `vkpi_monitored_products`.
2. Explicit `--apply` behavior creates a monitored product.
3. A product-specific suggestion is assigned once to Daily Top100.
4. Duplicate assignment remains zero.
5. Marker data is cleaned.

## Verification

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile \
  scripts/audit_vkpi_daily_top100_source.py \
  scripts/smoke_vkpi_daily_top100_source_trigger.py

PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_daily_top100_source.py

./scripts/run_smoke.sh smoke_vkpi_daily_top100_source_trigger.py
./scripts/run_smoke.sh \
  smoke_vkpi_daily_digest_staff_scope.py \
  smoke_vkpi_daily_digest_unique_assignment.py \
  smoke_vkpi_daily_digest_kol_pool_bridge.py \
  smoke_vkpi_daily_digest_responsible_import.py
```

Results:

- `py_compile`: PASS
- `smoke_vkpi_daily_top100_source_trigger.py`: PASS
- Daily Top100 regression group: PASS=4 / FAIL=0 / TOTAL=4

Live audit output:

```text
status=blocked
blockers=no_monitored_products,suggestions_are_bridge_only,no_local_product_candidates
monitored_products=0 enabled=0
bridge_or_blank_suggestions=90
product_candidates: none
```

## Operational Next Step

Configure real monitored products explicitly before relying on morning sync for new product-specific candidates, for example:

```bash
PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_daily_top100_source.py \
  --apply \
  --product-sku "VILTROX-AF-35MM-F1.8-EVO-FE" \
  --product-name "Viltrox AF 35mm F1.8 EVO FE" \
  --platforms youtube,instagram,tiktok
```

Then run `analytics_monitor` / `morning_sync` with a small max video count to create product-specific suggestions.

## Acceptance

- Audit reports real blockers clearly.
- No live API or crawler spend is used by diagnostics.
- Bridge data remains labelled as bridge data.
- Monitored product seeding is explicit, reversible, and not automatic.
