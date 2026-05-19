# V-KPI P4-1 New Launch Match Dry-Run

## 1. Scope Boundary

P4-1 only implements one recommendation scenario:

```text
new_launch_match
```

P4-1 must not implement or describe additional recommendation scenarios. The dry-run produces reviewable recommendation previews for a single launch/product query, using deterministic Memory facts and links only.

P4-1 explicitly does not do:

```text
LLM reason generation
database writes to recommendation tables
frontend UI
task allocation
multi-scenario recommendation abstraction
provider calls
```

P4-1 outputs JSON and Markdown files only. It is preview-only and defaults to dry-run.

## 2. Input Flow

P4-1 reads raw Memory and committed KOL data directly. It must not call P3 helper endpoints or helper scoring APIs as candidate input.

Allowed sources:

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
  fact_type='product_normalization'

vkpi_legacy_kol_entities
  weak_label
  resolution_decision
  entity_uid

vkpi_kol_pool
  id
  platform
  handle
  display_name
  source_ref
  sync_status
```

Forbidden inputs:

```text
GET /api/admin/vkpi/memory/product-kol-candidates
GET /api/admin/vkpi/memory/entities/{uid}/fit-features
scripts/build_vkpi_memory.py --product-kol-candidates
scripts/build_vkpi_memory.py --fit-features
```

Preflight may call/read readiness only as a gate:

```text
readiness.status must be ready_for_p4_dry_run
readiness.provider_calls_allowed must be false
```

Readiness is not a scoring source.

Data flow:

```text
product_query
  -> normalize to product/product_family candidates from Memory
  -> collect launch_plan and market_signal facts for matched family
  -> collect all KOL Memory entities
  -> join KOL worked_on_product links
  -> join product_family normalization links
  -> join KOL facts and committed entity review state
  -> compute score_breakdown
  -> build evidence_pro and evidence_con
  -> hard-filter invalid candidates
  -> rank and percentile
  -> write JSON preview
  -> write Markdown report
```

## 3. Scoring Formula

P4-1 uses a deterministic base score of 90 points, then applies multiplicative penalties. The final score is rounded to one decimal place.

### 3.1 Base Score

```text
product_match           25
cooperation_strength    15
market_signal           15
region_match            10
contact_availability    10
recency_boost           10
data_freshness           5
```

### 3.2 product_match, 25 points

Measures whether the KOL has historical evidence around the target product family.

```text
direct family match                 25
adjacent family match               15
same product type, different family 10
no product-family match              0
```

Direct match means a `worked_on_product` link connects the KOL to a product entity that has a `normalized_to_product_family` link to the target family.

Adjacent family match is deterministic and string-based for P4-1:

```text
same mount or same focal-length family token
```

Same product type means the candidate and target share a broad product token such as lens family or accessory category, but not the same normalized family.

### 3.3 cooperation_strength, 15 points

Measures total historical cooperation volume for this KOL, independent of the target product.

```text
10 or more cooperation facts/links   15
5-9                                  10
2-4                                   6
1                                     3
0                                     0
```

This dimension must count unique cooperation source references. Duplicate facts from the same `source_ref` count once.

### 3.4 market_signal, 15 points

Measures launch and market activity for the target family.

```text
launch_plan within 30 days           10
official_content within 90 days       5
otherwise                             0
```

The two sub-scores can stack to 15. Source facts must be tied to the target product family or one of its normalized product members.

### 3.5 region_match, 10 points

Measures KOL country against target markets provided by CLI.

```text
primary target market                10
secondary target market               6
other known market                    3
missing country                       5
```

If no target markets are passed, `missing country` remains neutral at 5 and known countries receive 3 unless a later explicit market list is provided in the CLI arguments.

### 3.6 contact_availability, 10 points

Uses Memory `contact_status` and `vkpi_kol_pool` contact-derived metadata where available.

```text
email and phone                      10
email only                            7
DM only                               4
missing                               0
unknown                               2
```

Restricted contact fields must not be printed in JSON or Markdown. The output only shows contact availability status.

### 3.7 recency_boost, 10 points

Measures recent KOL activity using KOL-linked activity/content facts when available. Official account content is not KOL activity.

```text
activity within 30 days              10
activity within 60 days               7
activity within 90 days               4
no activity signal                    0
```

If P4-1 cannot find a KOL-linked activity fact, the value is 0 and the item must add `no_recent_activity_signal` to `evidence_con`.

### 3.8 data_freshness, 5 points

Uses the KOL `evidence_count` fact.

```text
evidence_count >= 10                  5
evidence_count 5-9                    3
evidence_count < 5                    1
missing evidence_count                0
```

### 3.9 Penalty Factors

Penalties are multiplicative and applied after the base score.

```text
needs_human_review                   x 0.85
risk_flag > 0                        x 0.70
resolution_decision='escalate'       x 0.90
blocked_risk                         hard exclude
dropped                              hard exclude
```

Penalty factors stack:

```text
final_score = base_score * product_of_penalty_factors
```

`blocked_risk` and `dropped` candidates are excluded before ranking.

## 4. Evidence Chain

Every returned recommendation must include both supporting and opposing evidence arrays.

Hard rule:

```text
len(evidence_pro) + len(evidence_con) >= 3
```

Candidates with fewer than three total evidence items are excluded from output.

### 4.1 Evidence Item Schema

```json
{
  "type": "cooperation",
  "polarity": "pro",
  "severity": "info",
  "detail": "Worked on AF 35mm family via 4 cooperation records",
  "score_component": "product_match",
  "source_table": "vkpi_memory_links",
  "source_id": "12345",
  "source_ref": "legacy_batch:...",
  "source_sheet": "AF 35mm",
  "source_row": 42,
  "confidence_score": 1.0
}
```

Required fields:

```text
type
polarity
severity
detail
score_component
source_table
source_id
source_ref
confidence_score
```

`source_sheet` and `source_row` are required when the evidence comes from legacy Excel-derived facts, links, or source JSON.

### 4.2 Supporting Evidence

Allowed `evidence_pro` types:

```text
family_match
adjacent_family_match
cooperation
cooperation_strength
launch_signal
official_content_signal
region_match
contact_available
recent_activity
data_freshness
```

### 4.3 Opposing Evidence

Required `evidence_con` types when applicable:

```text
contact_missing
sync_needs_review
resolution_escalate
risk_flag
no_recent_activity_signal
competitor_collab
poor_historical_performance
missing_country
low_evidence_count
```

If `sync_status='needs_human_review'`, add `sync_needs_review`.

If `resolution_decision='escalate'`, add `resolution_escalate`.

If risk flags exist, add `risk_flag` and apply the risk penalty.

If no KOL-linked activity exists within 90 days, add `no_recent_activity_signal`.

If contact availability is missing, add `contact_missing`.

## 5. JSON Output Format

Top-level JSON:

```json
{
  "scenario": "new_launch_match",
  "mode": "dry_run",
  "generated_at": "2026-05-19T00:00:00Z",
  "product_query": "AF 35mm F1.2 LAB FE",
  "target_family_uid": "mem_product_family_xxx",
  "target_family_name": "AF 35mm F1.2 LAB",
  "provider_calls_allowed": false,
  "budget_guard": {
    "scope": "cron:p4_recommendations_daily",
    "estimated_cost_usd": 0.0,
    "allowed": true,
    "recorded_cost": false
  },
  "summary": {
    "total_candidates_evaluated": 1012,
    "excluded_blocked_or_dropped": 6,
    "excluded_low_evidence": 0,
    "returned": 100,
    "markdown_display_count": 67,
    "top_score": 88.5,
    "median_score": 52.0
  },
  "score_distribution": {
    "p90_plus": 8,
    "p75_to_p90": 23,
    "p50_to_p75": 36,
    "below_p50": 33
  },
  "items": []
}
```

Item JSON:

```json
{
  "rank": 1,
  "percentile_rank": 99,
  "kol_entity_uid": "mem_kol_xxx",
  "legacy_entity_uid": "legacy_kol_xxx",
  "kol_pool_id": 1234,
  "platform": "youtube",
  "handle": "example_handle",
  "display_name": "Example KOL",
  "country": "Germany",
  "score": 88.5,
  "review_required": false,
  "hard_excluded": false,
  "score_breakdown": {
    "product_match": 25,
    "cooperation_strength": 10,
    "market_signal": 15,
    "region_match": 10,
    "contact_availability": 7,
    "recency_boost": 10,
    "data_freshness": 5,
    "base": 82,
    "penalty_factors": {
      "needs_human_review": 1.0,
      "risk_flag": 1.0,
      "resolution_escalate": 1.0
    },
    "penalty_factor": 1.0,
    "final": 82.0
  },
  "evidence_pro": [],
  "evidence_con": [],
  "links": {
    "open_in_vkpi": "/kol/mem_kol_xxx"
  }
}
```

Rules:

```text
JSON includes Top N after hard filters.
Default Top N is 100.
Each item includes percentile_rank.
Each item includes score_breakdown.
Each item includes evidence_pro and evidence_con.
No private email or phone value is printed.
```

## 6. Markdown Report Template

Markdown output must use this structure exactly:

```markdown
# P4-1 New Launch Match Dry-Run

**Product:** <product_query>
**Target family:** <target_family_name>
**Generated at:** <generated_at>
**Mode:** dry_run
**Provider calls allowed:** false
**Budget scope:** cron:p4_recommendations_daily
**Estimated provider cost:** 0.0

## Summary

- Total candidates evaluated: <n>
- Returned in JSON: <n>
- Displayed in Markdown: <n score >= P50>
- Excluded blocked/dropped: <n>
- Excluded low evidence: <n>
- Top score: <score>
- Median score: <score>

## Score Distribution

- P90+: <n> candidates
- P75-P90: <n> candidates
- P50-P75: <n> candidates
- Below P50: <n> candidates

## Top Recommendations

### 1. @<handle> (score=<score>, percentile=<percentile>)

**Platform:** <platform>
**Country:** <country>
**Review required:** <true|false>

**Supporting evidence:**
- <type>: <detail> [<source_table>:<source_id>]
- <type>: <detail> [<source_table>:<source_id>]
- <type>: <detail> [<source_table>:<source_id>]

**Concerns:**
- <type>: <detail> (severity=<severity>)

**Score breakdown:** product=<n> cooperation=<n> market=<n> region=<n> contact=<n> recency=<n> freshness=<n> -> base=<n> x penalty=<n> = <final>

[Open in V-KPI: /kol/<kol_entity_uid>]

---
```

Markdown display rule:

```text
Only items with score >= P50 are shown in Markdown.
JSON still includes Top N after hard filters.
```

## 7. CLI Design

Single command:

```bash
python3 scripts/p4_new_launch_match.py \
  --product "AF 35mm F1.2 LAB FE" \
  --limit 100 \
  --primary-markets "Germany,United States,Japan" \
  --secondary-markets "United Kingdom,France,Canada" \
  --json-out /tmp/p4_new_launch_match.json \
  --md-out /tmp/p4_new_launch_match.md \
  --dry-run
```

Arguments:

```text
--product              required product or launch query
--limit                optional, default 100, max 500
--primary-markets      optional comma-separated country list
--secondary-markets    optional comma-separated country list
--json-out             optional output path; if omitted, print JSON to stdout
--md-out               optional output path; if omitted, skip Markdown file
--dry-run              optional flag, default true
--commit               forbidden in P4-1
```

P4-1 must reject write-mode arguments:

```text
--commit
--write-db
--provider-call
```

Default behavior:

```text
dry_run=true
database_writes=false
provider_calls=false
```

## 8. Budget Guard Hook

P4-1 calls Budget Guard as a zero-cost preflight.

Required behavior:

```text
scope = cron:p4_recommendations_daily
estimated_cost = 0.0
check_budget is called before scoring
record_cost is not called
vkpi_ai_cost_ledger is not written
```

Pseudo-flow:

```python
cost_ok = check_budget("cron:p4_recommendations_daily", estimated_cost=0.0)
if not cost_ok:
    return {"status": "budget_guard_blocked"}

candidates = build_candidates_from_memory(product_query)
scored = score_candidates(candidates)
write_json(scored)
write_markdown(scored)
```

Acceptance for the hook:

```text
vkpi_ai_cost_ledger count remains unchanged
provider_calls_allowed remains false
budget_caps has at least 5 configured scopes
```

## 9. Acceptance Gates

P4-1 is accepted only when all of these pass:

```text
1. Produces 50-100 recommendation preview items for a valid launch query.
2. Makes 0 LLM/provider calls.
3. Leaves vkpi_ai_cost_ledger unchanged.
4. Performs 0 recommendation-table writes.
5. Every returned item has at least 3 total evidence items.
6. Every returned item has evidence_pro, evidence_con, and score_breakdown.
7. Every score component is deterministic and explainable from Memory rows.
8. CLI defaults to dry-run and rejects write/provider-call flags.
```

Red lines:

```text
product-kol-candidates helper is called
fit-features helper is called
provider call occurs
AI cost ledger row is inserted
recommendation table is written
candidate lacks evidence chain
score cannot be traced to Memory rows
```

## 10. One-Line Route After P4-1

```text
P4-2: add LLM wording for reasons while keeping P4-1 deterministic ranking.
P4-3: persist reviewed recommendation runs/items/evidence.
P4-4: add a frontend preview and review surface.
P4-5: add additional recommendation scenarios.
```
