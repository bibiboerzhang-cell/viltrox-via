# V-KPI P4-6 KOL Product Fit Acceptance

## Scope

P4-6 implements deterministic `kol_product_fit` dry-run only.

It does not include:

```text
LLM reasons
persisted recommendation runs
frontend UI
project/staff next-action suggestions
```

## Verified Command

```bash
python3 scripts/p4_kol_product_fit.py \
  --kol-pool-id 1539 \
  --limit 10 \
  --json-out /tmp/p4_6_kol_product_fit.json \
  --md-out /tmp/p4_6_kol_product_fit.md
```

Observed output:

```text
scenario=kol_product_fit
mode=dry_run
kol=media:blog
kol_entity_uid=mem_kol_2aa64268eb12122a3dfc
kol_pool_id=1539
provider_calls_allowed=false
budget_scope=cron:p4_recommendations_daily
budget_allowed=true
budget_recorded_cost=false
total_families_evaluated=659
eligible_after_hard_filters=597
returned=10
markdown_display_count=7
excluded_inactive_or_empty_family=62
excluded_low_evidence=0
top_score=87.0
median_score=72.0
```

Top 3:

```text
1. AF 35mm F1.2 LAB  score=87.0
2. AF 35mm F1.8 EVO  score=87.0
3. AF 55mm F1.8 EVO  score=82.0
```

## Validation Results

```text
returned=10
min_evidence=7
missing_breakdown=0
missing_pro_con=0
markdown_has_top=true
```

Database side effects:

```text
new_launch_match_v1 runs=1      # unchanged from P4-3 smoke
previewed recommendations=3     # unchanged from P4-3 smoke
vkpi_ai_cost_ledger calls=0
vkpi_ai_cost_ledger spend=0
```

Forbidden flag check:

```bash
python3 scripts/p4_kol_product_fit.py --kol-pool-id 1539 --commit
```

Expected error:

```text
P4-6 dry-run rejects write/provider flags: --commit
```

## Acceptance Gates

```text
1. CLI resolves a KOL by kol_pool_id.
2. Candidate source is product_family rows from Memory.
3. Returned rows include score_breakdown.
4. Returned rows include evidence_pro and evidence_con.
5. Every returned row has at least 3 evidence items.
6. Budget Guard is checked with estimated_cost_usd=0.
7. AI cost ledger remains unchanged.
8. Recommendation tables remain unchanged.
9. Forbidden write/provider flags are rejected.
10. JSON and Markdown reports are produced.
```
