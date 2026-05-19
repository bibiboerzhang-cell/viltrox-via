# V-KPI P4-12 Project Next-Action Reasons

## Scope

P4-12 adds optional reason wording to `project_next_action`.

It does not change:

```text
candidate project source
scoring formula
rank order
database writes
project actions
task assignment
stage transitions
```

## CLI

```bash
VKPI_LLM_GATEWAY_FORCE_OFFLINE=1 python3 scripts/p4_project_next_action.py \
  --limit 3 \
  --include-unassigned \
  --with-llm-reasons \
  --reason-limit 2 \
  --json-out /tmp/p4_12_project_next_action_reasons.json \
  --md-out /tmp/p4_12_project_next_action_reasons.md
```

## Budget Scope

```text
purpose=p4_recommendation_reasons
cost_tag=cron:p4_recommendation_reasons
```

Offline, disabled, or blocked provider paths must attach deterministic fallback reasons and keep `vkpi_ai_cost_ledger` unchanged.

## Acceptance Gates

```text
1. Default P4-11 output still has no recommendation_reason blocks.
2. --with-llm-reasons attaches reasons to min(reason_limit, returned) rows.
3. Ranking is unchanged by reason generation.
4. Offline fallback mode is deterministic_fallback.
5. AI cost ledger remains 0 for offline fallback.
6. Markdown includes Recommendation reason blocks for enriched rows.
7. No project/task/recommendation rows are written.
```
