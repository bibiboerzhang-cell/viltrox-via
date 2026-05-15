# V-KPI P4 Step29 - Daily Top100 Source Diagnostics

Date: 2026-05-14
Repo: `/Users/bibiboer/Documents/V-KPI——marketing`
Branch: `codex/vkpi-cleanup-d7`

## Scope

This step audited Daily Top100 source, digest generation, and staff coverage.
It did not change product code.

## Current Database Snapshot

| Table | Count |
|---|---:|
| `vkpi_outreach_suggestions` | 96 |
| `vkpi_staff_outreach_digests` | 12 |
| `vkpi_staff_outreach_digest_items` | 99 |
| `staff` | 2 |
| `kols` | 16 |
| `vkpi_kol_pool` | 90 |
| `vkpi_monitored_products` | 1 |

## Candidate Source

Daily Top100 currently has real candidate source data.

| Source SKU | Count |
|---|---:|
| `kol_pool` | 90 |
| `AF-35-55-F1.8-EVO-FE-Z` | 6 |

`scripts/audit_vkpi_daily_top100_source.py --json --limit 100` returned:

- `status=ok`
- `blockers=[]`
- `monitored_products_count=1`
- `enabled_monitored_products_count=1`
- `real_suggestion_skus=["AF-35-55-F1.8-EVO-FE-Z"]`
- `next_actions=["No source blocker detected; continue with endpoint/browser QA for Daily Top100."]`

## Digest Status

Service-level status for all SKUs:

- `feature_enabled=True`
- `active_staff_count=2`
- `eligible_staff_count=2`
- `generated_staff_count=2`
- `ready_staff_count=2`
- `empty_staff_count=0`
- `candidate_source=outreach_suggestions`
- `total_candidates=96`
- `items_total=5`
- `duplicate_suggestion_count=0`
- `assignment_strategy=owner_first_then_round_robin`
- `last_generated_at=2026-05-14T07:34:31Z`

Service-level status for product `AF-35-55-F1.8-EVO-FE-Z`:

- `total_candidates=6`
- `items_total=5`
- `generated_staff_count=2`
- `ready_staff_count=2`
- `duplicate_suggestion_count=0`

## Main Finding

The old `0/11 employees` wording is stale for the current local dataset.
The current database only has 2 active staff rows, and Daily Top100 currently covers `2/2`.

This does not mean future employee provisioning is complete.
It means the current bug is not "Daily Top100 source is empty"; the current remaining work is endpoint/browser QA and real user provisioning policy.

## Verification

Command:

```bash
./scripts/run_smoke.sh \
  smoke_vkpi_daily_digest_kol_pool_bridge.py \
  smoke_vkpi_daily_digest_staff_scope.py \
  smoke_vkpi_daily_digest_unique_assignment.py \
  smoke_vkpi_daily_digest_responsible_import.py \
  smoke_vkpi_daily_top100_source_trigger.py \
  smoke_vkpi_p4_3_daily_top100_source_gate.py \
  smoke_vkpi_p3_11c_daily_top100_ui_contract.py
```

Result:

- `PASS=7`
- `FAIL=0`
- `TOTAL=7`

## Remaining Work

Daily Top100 next step should not be another backend rewrite.

Recommended next checks:

- Browser QA: manager view shows `2/2` or current active staff count, not `0/11`.
- Endpoint QA: `/api/admin/vkpi/analytics/daily-digest/status` matches service output.
- Provisioning policy: when more employees are created later, they must be explicitly active and eligible before expecting Top100 coverage.
- Product scope QA: UI product filter should show whether the current Top100 list is `kol_pool`, `AF-35-55-F1.8-EVO-FE-Z`, or all products.
