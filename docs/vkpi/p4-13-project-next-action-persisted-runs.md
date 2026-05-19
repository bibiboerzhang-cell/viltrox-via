# V-KPI P4-13 Project Next-Action Persisted Runs

## Scope

P4-13 persists `project_next_action` preview output into existing recommendation run tables.

It only writes:

```text
vkpi_kol_recommendation_runs
vkpi_kol_recommendations
vkpi_recommendation_explanations
```

It does not:

```text
assign tasks
transition project stages
send messages
write workflow rows
write recommendation outcomes
```

## Strategy

Persisted runs use:

```text
strategy_version=project_next_action_v1
status=previewed
```

Each item stores:

```text
handle                  project:<project_id>
display_name            project_name
linked_main_kol_id      project.kol_id when available
feature_snapshot_json   project/action/counts snapshot
scoring_breakdown_json  deterministic score_breakdown
explanation_json        evidence_pro/evidence_con/recommendation_reason
```

## Verified Command

```bash
VKPI_LLM_GATEWAY_FORCE_OFFLINE=1 python3 scripts/p4_project_next_action.py \
  --limit 3 \
  --include-unassigned \
  --with-llm-reasons \
  --reason-limit 2 \
  --persist-run \
  --json-out /tmp/p4_13_project_next_action_persisted.json \
  --md-out /tmp/p4_13_project_next_action_persisted.md
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
3. strategy_version is project_next_action_v1.
4. run.status is previewed.
5. recommendation_count equals returned item count.
6. Every persisted recommendation has one explanation row.
7. Offline reason fallback keeps vkpi_ai_cost_ledger unchanged.
8. No project/task/stage/message rows are written.
```
