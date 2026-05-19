# V-KPI P10 Learning Loop v0

## Scope

P10 starts read-only.

P10-1 produces a learning snapshot from feedback and review surfaces. It does not change scoring.

## Inputs

```text
vkpi_recommendation_feedback
vkpi_recommendation_outcomes
vkpi_competitor_signals.review_status
vkpi_memory_feedback
vkpi_alerts.status
```

Current local state:

```text
recommendation_feedback=0
recommendation_outcomes=171
competitor_signals=25
memory_feedback=0
alerts=18
```

## Non-Goals

P10-1 does not:

```text
train a model
change recommendation weights
call providers
write learning tables
auto-approve pending signals
auto-resolve alerts
```

## Snapshot Output

```json
{
  "scenario": "p10_learning_snapshot",
  "provider_calls": false,
  "write_db": false,
  "readiness": {
    "recommendation_feedback_ready": false,
    "competitor_review_ready": false,
    "memory_feedback_ready": false,
    "outcome_data_ready": true
  },
  "gaps": [
    "recommendation feedback is empty",
    "competitor signals are pending review"
  ]
}
```

## Acceptance

```text
1. CLI prints a read-only learning snapshot.
2. provider_calls=false.
3. write_db=false.
4. AI cost ledger remains unchanged.
5. No frontend UI in P10-1.
```

## Package Plan

```text
P10-0 design read-only learning boundary
P10-1 service + CLI snapshot
P10-2 read-only API
P10-3 feedback gap dashboard
P10-4 scoring change proposal only after feedback exists
```
