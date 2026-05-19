# V-KPI P4-3 Persisted Preview Runs

## Scope

P4-3 persists a P4 `new_launch_match` preview into the existing recommendation run tables.

It only writes:

```text
vkpi_kol_recommendation_runs
vkpi_kol_recommendations
vkpi_recommendation_explanations
```

It does not create projects, assign staff, send outreach, update KOL profiles, or write feedback/outcome rows.

## CLI

Preview-only P4-1 remains the default:

```bash
python3 scripts/p4_new_launch_match.py \
  --product "AF 35mm F1.2 LAB FE" \
  --limit 100
```

Persisted preview run:

```bash
python3 scripts/p4_new_launch_match.py \
  --product "AF 35mm F1.2 LAB FE" \
  --limit 100 \
  --persist-run
```

Persisted preview with P4-2 reasons:

```bash
VKPI_LLM_GATEWAY_FORCE_OFFLINE=1 python3 scripts/p4_new_launch_match.py \
  --product "AF 35mm F1.2 LAB FE" \
  --limit 20 \
  --with-llm-reasons \
  --reason-limit 5 \
  --persist-run
```

`--persist-run` is the only P4-3 write switch. P4 still rejects the older generic write/provider flags:

```text
--commit
--write-db
--provider-call
```

## Stored Data

`vkpi_kol_recommendation_runs`:

```text
run_uid                 p4nlm-<token>
launch_id               NULL
strategy_version        new_launch_match_v1
status                  previewed
candidate_count         total candidates evaluated
recommendation_count    returned item count
filters_json            scenario/product/family/reason metadata
```

`vkpi_kol_recommendations`:

```text
recommendation_uid      p4nlm-rec-<token>
run_id                  persisted run id
kol_pool_id             linked pool id when available
platform/handle/name    preview identity fields
score/rank              deterministic P4 score and rank
status                  previewed
feature_snapshot_json   product/family/KOL source refs
scoring_breakdown_json  full score_breakdown
explanation_json        evidence_pro/evidence_con/recommendation_reason
```

`vkpi_recommendation_explanations`:

```text
explanation_type        p4_new_launch_match
explanation_text        short reason or fallback preview text
strengths_json          evidence_pro
concerns_json           evidence_con
model_version           reason model or rule_v1
```

## Acceptance Gates

```text
1. Without --persist-run, no recommendation rows are written.
2. With --persist-run, exactly one run row is written.
3. recommendation_count equals returned item count.
4. Each persisted recommendation has one explanation row.
5. Persisted recommendation status is previewed.
6. Stored score/rank match JSON output.
7. P4-2 reasons, when present, are stored inside explanation_json.
8. vkpi_ai_cost_ledger remains 0 for offline fallback tests.
```

## Verified Smoke Shape

```bash
VKPI_LLM_GATEWAY_FORCE_OFFLINE=1 python3 scripts/p4_new_launch_match.py \
  --product "AF 35mm F1.2 LAB FE" \
  --limit 3 \
  --with-llm-reasons \
  --reason-limit 2 \
  --persist-run \
  --json-out /tmp/p4_3_persisted.json \
  --md-out /tmp/p4_3_persisted.md
```

Expected summary:

```text
persistence_enabled=true
persisted_recommendations=3
llm_reasons_requested=true
reasons_attached=2
```
