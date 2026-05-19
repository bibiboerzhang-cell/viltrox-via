# V-KPI P4-5 KOL Product Fit Dry-Run

## Scope

P4-5 adds the second P4 recommendation scenario:

```text
kol_product_fit
```

It answers one question:

```text
Given one KOL, which product families should we consider for future cooperation?
```

P4-5 does not implement:

```text
new launch matching changes
project/staff next-action recommendations
frontend UI
task allocation
outreach automation
provider-only reasoning
automatic project creation
```

The first implementation must be a deterministic dry-run. Persistence can reuse the P4-3 `previewed` recommendation tables only after the preview output is verified.

## Inputs

One and only one KOL selector is required:

```text
--kol-entity-uid mem_kol_...
--kol-pool-id 1539
--platform youtube --handle example
```

Allowed data sources:

```text
vkpi_memory_entities
  entity_type='kol'
  entity_type='product'
  entity_type='product_family'
  entity_type='market_topic'

vkpi_memory_links
  link_type='worked_on_product'
  link_type='normalized_to_product_family'
  link_type='official_account_published_product'

vkpi_memory_facts
  fact_type='cooperation'
  fact_type='contact_status'
  fact_type='risk_flag'
  fact_type='sync_status'
  fact_type='weak_label'
  fact_type='country'
  fact_type='review_state'
  fact_type='evidence_count'
  fact_type='market_signal'
  fact_type='launch_plan'
  fact_type='product_cost'

vkpi_kol_pool
  id
  platform
  handle
  display_name
  country
  source_ref
  sync_status

vkpi_legacy_kol_entities
  entity_uid
  weak_label
  resolution_decision
```

Forbidden inputs:

```text
P3 product-kol-candidates helper
P4 new_launch_match output as candidate source
LLM-generated product suggestions as source of truth
frontend-only state
```

## Candidate Set

The candidate universe is product families, not individual SKUs.

Build product families from:

```text
vkpi_memory_entities.entity_type='product_family'
```

Then attach evidence from:

```text
products normalized to that family
KOL worked_on_product links
official account published product links
launch_plan / market_signal facts
product_cost facts when available
```

Rows with no evidence are allowed in JSON only if `--include-low-evidence` is passed. Markdown should default to evidence-backed rows.

## Scoring Formula

Base score is 90 points. Final score is base multiplied by risk/review penalties.

```text
historical_fit             25
adjacent_product_fit       15
cooperation_depth          15
market_activity            15
contact_readiness          10
region_relevance            5
data_quality                5
```

### historical_fit, 25

```text
KOL has direct cooperation with family       25
KOL has cooperation with normalized product  20
KOL has same focal/product type evidence     10
no historical fit                             0
```

### adjacent_product_fit, 15

This is not a duplicate of historical fit. It rewards expansion from proven nearby families.

```text
same focal length, different aperture/series 15
same broad product category                  10
same mount/ecosystem                          6
none                                          0
```

### cooperation_depth, 15

Counts unique cooperation source references for the KOL.

```text
10+ cooperations   15
5-9                10
2-4                 6
1                   3
0                   0
```

### market_activity, 15

Measures product-family market signals.

```text
launch_plan within 30 days      10
official content within 90 days  5
otherwise                        0
```

### contact_readiness, 10

Uses contact availability, never raw restricted contact fields.

```text
email and phone 10
email only       7
DM only          4
missing          0
unknown          2
```

### region_relevance, 5

Uses KOL country and optional target market filter.

```text
primary target market   5
secondary target market 3
known other market      2
missing country         2
```

### data_quality, 5

```text
evidence_count >= 10 5
evidence_count 5-9   3
evidence_count < 5   1
missing              0
```

## Penalties And Hard Filters

Hard filters:

```text
weak_label=blocked_risk
resolution_decision=drop
product family has no active product members
```

Multiplicative penalties:

```text
sync_status=needs_human_review      x 0.85
resolution_decision=escalate        x 0.90
risk_flag count > 0                 x 0.70
contact missing                     x 0.90
```

The score must never depend on hidden LLM output.

## Evidence Schema

Every returned product-family candidate must include both arrays:

```json
{
  "evidence_pro": [
    {
      "type": "historical_fit",
      "detail": "KOL worked on AF 35mm F1.2 LAB FE",
      "source_table": "vkpi_memory_links",
      "source_id": 123,
      "score_component": "historical_fit"
    }
  ],
  "evidence_con": [
    {
      "type": "contact_missing",
      "detail": "No usable contact status in Memory",
      "severity": "medium",
      "score_component": "contact_readiness"
    }
  ]
}
```

Minimum evidence gate:

```text
evidence_pro + evidence_con >= 3
```

## JSON Output

```json
{
  "scenario": "kol_product_fit",
  "mode": "dry_run",
  "kol": {
    "kol_entity_uid": "mem_kol_...",
    "kol_pool_id": 1539,
    "platform": "youtube",
    "handle": "example",
    "display_name": "Example"
  },
  "summary": {
    "total_families_evaluated": 659,
    "eligible_after_hard_filters": 120,
    "returned": 50,
    "top_score": 82.0,
    "median_score": 54.0
  },
  "items": [
    {
      "rank": 1,
      "percentile_rank": 99.9,
      "product_family_uid": "mem_product_family_...",
      "product_family_name": "AF 35mm F1.2 LAB",
      "score": 82.0,
      "score_breakdown": {},
      "evidence_pro": [],
      "evidence_con": []
    }
  ]
}
```

## CLI

```bash
python3 scripts/p4_kol_product_fit.py \
  --kol-pool-id 1539 \
  --limit 50 \
  --json-out /tmp/p4_5_kol_product_fit.json \
  --md-out /tmp/p4_5_kol_product_fit.md
```

Optional:

```text
--kol-entity-uid <uid>
--platform <platform> --handle <handle>
--primary-markets "Germany,United States"
--secondary-markets "Japan,United Kingdom"
--include-low-evidence
```

Default must be dry-run and must not write database rows.

Deferred flags for later packages:

```text
--with-llm-reasons   P4.7
--reason-limit 10    P4.7
--persist-run        P4.8
```

`--persist-run` will be the only allowed write switch when P4.8 adds persistence. It will store `status=previewed`.

Rejected flags:

```text
--commit
--write-db
--provider-call
```

## Budget Guard

The deterministic dry-run uses:

```text
scope=cron:p4_recommendations_daily
estimated_cost_usd=0.0
```

Optional reasons use:

```text
scope=cron:p4_recommendation_reasons
```

Offline or blocked provider paths must fall back to deterministic reason text and leave `vkpi_ai_cost_ledger` unchanged.

## Acceptance Gates

```text
1. CLI resolves exactly one KOL selector.
2. Dry-run evaluates product families directly from Memory tables.
3. No P3 helper or P4 new_launch_match output is used as candidate source.
4. Returned rows include score_breakdown, evidence_pro, and evidence_con.
5. Each returned row has at least 3 evidence items unless --include-low-evidence is passed.
6. Default run writes no recommendation rows.
7. P4.6 does not expose --persist-run yet.
8. P4.6 does not expose --with-llm-reasons yet.
9. Forbidden write/provider flags are rejected.
10. Frontend/API work is out of scope for the first code package.
```

## Route After P4-5

```text
P4.6 implement kol_product_fit dry-run CLI
P4.7 add optional budget-gated reasons for kol_product_fit
P4.8 persist kol_product_fit preview runs
P4.9 expose/list both P4 scenarios from the same run API
P4.10 design project/staff next-action suggestions
```
