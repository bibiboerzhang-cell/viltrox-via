# V-KPI P10-1 Learning Snapshot CLI

## Scope

P10-1 adds a read-only learning snapshot service and CLI.

It reads:

```text
vkpi_recommendation_feedback
vkpi_recommendation_outcomes
vkpi_competitor_signals
vkpi_memory_feedback
vkpi_alerts
```

It does not:

```text
write learning rows
change scoring weights
call providers
resolve alerts
approve competitor signals
```

## Files

```text
backend/app/services/vkpi/learning_loop.py
scripts/p10_learning_snapshot.py
docs/vkpi/p10-1-learning-snapshot-cli.md
```

## CLI

```bash
python3 scripts/p10_learning_snapshot.py
python3 scripts/p10_learning_snapshot.py \
  --json-out /tmp/p10_learning_snapshot.json \
  --md-out /tmp/p10_learning_snapshot.md
```

## Current Snapshot

```text
scenario=p10_learning_snapshot
provider_calls=false
write_db=false
competitor_signals=25
memory_feedback=0
recommendation_feedback=0
readiness.competitor_review_ready=false
readiness.memory_feedback_ready=false
readiness.outcome_data_ready=true
readiness.recommendation_feedback_ready=false
```

Current gaps:

```text
recommendation_feedback_empty
memory_feedback_empty
competitor_signals_pending_review
open_alerts_need_resolution
recommendation_outcomes_have_no_shortlist_actions
```

## Acceptance

```text
python3 -m py_compile backend/app/services/vkpi/learning_loop.py passed
python3 -m py_compile scripts/p10_learning_snapshot.py passed
CLI printed Markdown snapshot
json_out and md_out were generated
provider_calls=false
write_db=false
```

## Next

P10-2 can expose this snapshot through a read-only API.

Do not change scoring until feedback rows exist.
