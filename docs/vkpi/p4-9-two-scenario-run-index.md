# V-KPI P4-9 Two-Scenario Run Index

## Scope

P4-9 verifies that the persisted preview run index works for both P4 scenarios:

```text
new_launch_match_v1
kol_product_fit_v1
```

No schema change, router change, or frontend layout change is required. P4-4 already added:

```text
GET /api/admin/vkpi/product-analysis/recommendation-runs
```

with `strategy_version` and `status` filters.

## Verification

Service-level query:

```python
product_analysis.list_recommendation_runs(
    strategy_version="new_launch_match_v1",
    status="previewed",
    limit=10,
)

product_analysis.list_recommendation_runs(
    strategy_version="kol_product_fit_v1",
    status="previewed",
    limit=10,
)
```

Observed:

```text
new_launch_match_v1 runs=1
  id=518
  run_uid=p4nlm-d8091029270e3230
  status=previewed
  candidate_count=1012
  recommendation_count=3
  filters_keys=[
    dry_run,
    llm_reasons_requested,
    product_query,
    reason_count,
    scenario,
    source_mode,
    target_family_name,
    target_family_uid
  ]
  counts.previewed=3

kol_product_fit_v1 runs=1
  id=519
  run_uid=p4kpf-b6b5445fea4dca44
  status=previewed
  candidate_count=659
  recommendation_count=3
  filters_keys=[
    dry_run,
    kol,
    llm_reasons_requested,
    reason_count,
    scenario,
    source_mode
  ]
  counts.previewed=3
```

## Acceptance Gates

```text
1. new_launch_match_v1 preview run can be listed.
2. kol_product_fit_v1 preview run can be listed.
3. Both runs include parsed filters.
4. Both runs include recommendation_status_counts.
5. Existing recommendation item endpoint can still use run_id.
6. No additional API namespace is introduced.
```
