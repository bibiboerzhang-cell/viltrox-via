# V-KPI P4-8 KOL Product Fit Persisted Runs

## Scope

P4-8 persists `kol_product_fit` preview output into the existing recommendation run tables.

It only writes:

```text
vkpi_kol_recommendation_runs
vkpi_kol_recommendations
vkpi_recommendation_explanations
```

It does not create projects, assign staff, change KOL profiles, or write recommendation outcomes.

## Strategy

Persisted runs use:

```text
strategy_version=kol_product_fit_v1
status=previewed
```

Each item stores:

```text
kol_pool_id             selected KOL
platform/handle         selected KOL identity
display_name            candidate product_family_name
feature_snapshot_json   selected KOL + product family snapshot
scoring_breakdown_json  deterministic score_breakdown
explanation_json        evidence_pro/evidence_con/recommendation_reason
```

## Verified Command

```bash
VKPI_LLM_GATEWAY_FORCE_OFFLINE=1 python3 scripts/p4_kol_product_fit.py \
  --kol-pool-id 1539 \
  --limit 3 \
  --with-llm-reasons \
  --reason-limit 2 \
  --persist-run \
  --json-out /tmp/p4_8_kol_product_fit_persisted.json \
  --md-out /tmp/p4_8_kol_product_fit_persisted.md
```

Expected summary:

```text
persistence_enabled=true
persisted_recommendations=3
llm_reasons_requested=true
reasons_attached=2
```

## Acceptance Gates

```text
1. Without --persist-run, no recommendation rows are written.
2. With --persist-run, exactly one run is written.
3. strategy_version is kol_product_fit_v1.
4. run.status is previewed.
5. recommendation_count equals returned item count.
6. Every persisted recommendation has one explanation row.
7. Offline reason fallback keeps vkpi_ai_cost_ledger unchanged.
8. Existing new_launch_match persisted runs are unaffected.
```
