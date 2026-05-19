# V-KPI P4-11 Project Next-Action Acceptance

## Scope

P4-11 implements deterministic `project_next_action` dry-run only.

It does not:

```text
assign tasks
transition project stages
send messages
write recommendation rows
call providers
```

## Verified Command

```bash
python3 scripts/p4_project_next_action.py \
  --limit 20 \
  --include-unassigned \
  --json-out /tmp/p4_11_project_next_action.json \
  --md-out /tmp/p4_11_project_next_action.md
```

Observed summary:

```text
scenario=project_next_action
mode=dry_run
provider_calls_allowed=false
budget_recorded_cost=false
projects_evaluated=3
eligible_after_hard_filters=3
returned=3
markdown_display_count=3
excluded_unassigned=0
excluded_low_evidence=0
top_score=42.0
median_score=37.0
sample.1=rank:1 score:42.0 priority:low project:3620 stage:discovery action:follow_up_message pro:3 con:2
sample.2=rank:2 score:37.0 priority:low project:3622 stage:discovery action:follow_up_message pro:3 con:2
sample.3=rank:3 score:37.0 priority:low project:3621 stage:contacted action:follow_up_message pro:3 con:2
```

Validation:

```text
returned=3
min_evidence=5
missing_breakdown=0
missing_ops=0
markdown_has_top=true
vkpi_projects count=14
P4 persisted recommendation runs=2
vkpi_ai_cost_ledger calls=0
vkpi_ai_cost_ledger spend=0
```

The local database did not have `vkpi_async_tasks` materialized during this smoke, so task-row count was not used as an acceptance signal. The CLI still rejects task-writing flags before execution.

Forbidden flag check:

```bash
python3 scripts/p4_project_next_action.py --assign-task --limit 1
```

Expected error:

```text
P4-11 dry-run rejects action/provider flags: --assign-task
```

## Acceptance Gates

```text
1. Reads project/workflow rows only.
2. Produces JSON and Markdown.
3. Every returned suggestion includes score_breakdown.
4. Every returned suggestion includes evidence_pro and evidence_con.
5. Default path writes no project/task/recommendation rows.
6. AI cost ledger remains unchanged.
7. --assign-task, --transition-stage, --send-message, --commit, --write-db, and --provider-call are rejected.
```
