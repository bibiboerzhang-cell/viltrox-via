# V-KPI P4-2 Recommendation Reasons

## Scope

P4-2 only adds optional recommendation reasons to the P4-1 `new_launch_match` dry-run output.

P4-2 does not change:

```text
candidate sourcing
scoring formula
ranking
hard filters
recommendation database writes
frontend UI
```

The P4-1 deterministic ranking remains the source of truth. P4-2 enriches the already-returned top candidates with a short reason block for review.

## CLI

Default P4-1 behavior remains provider-free:

```bash
python3 scripts/p4_new_launch_match.py \
  --product "AF 35mm F1.2 LAB FE" \
  --limit 100 \
  --json-out /tmp/p4_1.json \
  --md-out /tmp/p4_1.md
```

P4-2 reasons are opt-in:

```bash
python3 scripts/p4_new_launch_match.py \
  --product "AF 35mm F1.2 LAB FE" \
  --limit 100 \
  --with-llm-reasons \
  --reason-limit 20 \
  --json-out /tmp/p4_2.json \
  --md-out /tmp/p4_2.md
```

`--reason-limit` caps how many returned candidates receive a reason. It does not affect ranking or the number of returned recommendation rows.

## Budget Guard

P4-2 uses the LLM gateway with:

```text
purpose=p4_recommendation_reasons
cost_tag=cron:p4_recommendation_reasons
```

The gateway must check all relevant budget scopes before any real provider call:

```text
monthly_total
provider:<mapped-provider>
cron:p4_recommendation_reasons
```

If budget is disabled, missing, exhausted, or the provider call fails, the CLI keeps the recommendation row and attaches a deterministic fallback reason.

Fallback reason generation must not write to `vkpi_ai_cost_ledger` because no provider cost was incurred.

## Output Shape

Rows enriched by P4-2 add:

```json
{
  "recommendation_reason": {
    "mode": "llm",
    "provider": "openai",
    "model": "gpt-...",
    "status": "success",
    "fallback_reason": "",
    "short_reason": "...",
    "pitch_angle": "...",
    "caution_note": "..."
  }
}
```

Fallback rows use:

```json
{
  "recommendation_reason": {
    "mode": "deterministic_fallback",
    "provider": "rule_v0",
    "model": "rule_v0",
    "status": "fallback_to_rule",
    "fallback_reason": "budget_disabled",
    "short_reason": "...",
    "pitch_angle": "...",
    "caution_note": "..."
  }
}
```

The Markdown report prints the same three review fields under `Recommendation reason`.

## Prompt Contract

The LLM prompt receives only:

```text
scenario
product query
target family
KOL platform / handle / display name / country / score / review flag
score_breakdown
top evidence_pro rows
top evidence_con rows
```

It must return strict JSON with:

```text
short_reason
pitch_angle
caution_note
```

The prompt forbids invented facts. If parsing fails, P4-2 falls back to deterministic text.

## Acceptance Gates

```text
1. P4-1 command without --with-llm-reasons still returns the same ranking shape.
2. P4-2 only attaches reasons to min(reason_limit, returned) rows.
3. Ranking, score, evidence_pro, and evidence_con are unchanged by reason generation.
4. Budget scope cron:p4_recommendation_reasons is used for reason calls.
5. Offline or blocked provider path returns deterministic_fallback reasons.
6. Offline fallback keeps vkpi_ai_cost_ledger at 0.
7. JSON output includes recommendation_reason for enriched rows only.
8. Markdown output includes Recommendation reason blocks for enriched rows.
9. Forbidden write flags remain rejected.
10. No recommendation database tables are written in P4-2.
```

## Verified Offline Smoke

```bash
VKPI_LLM_GATEWAY_FORCE_OFFLINE=1 python3 scripts/p4_new_launch_match.py \
  --product "AF 35mm F1.2 LAB FE" \
  --limit 5 \
  --with-llm-reasons \
  --reason-limit 3 \
  --json-out /tmp/p4_2_reasons.json \
  --md-out /tmp/p4_2_reasons.md
```

Expected summary:

```text
returned=5
llm_reasons_requested=true
reasons_attached=3
recommendation_reason.mode=deterministic_fallback
vkpi_ai_cost_ledger total_calls=0
```
