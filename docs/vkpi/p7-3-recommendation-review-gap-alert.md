# V-KPI P7-3 Recommendation Review Gap Alert

## Scope

P7-3 adds one review-loop anomaly rule:

```text
recommendation.review_gap
```

It reads only:

```text
vkpi_kol_recommendation_runs
vkpi_kol_recommendations
vkpi_recommendation_feedback
```

It reuses:

```text
vkpi_alerts
backend/app/services/vkpi/alerts.py
```

It does not:

```text
call LLM providers
run recommendation scoring
write recommendation feedback
write vkpi_ai_cost_ledger
create a new alert table
create frontend UI
```

## Trigger Logic

For each recommendation run:

```text
run.status in ('previewed', 'completed')
created_at <= now - min_age_hours
recommendation_rows > 0
feedback_rows = 0
```

Default:

```text
min_age_hours = 1
```

Severity:

```text
danger  if recommendation_rows >= 50
warning otherwise
```

Alert identity:

```text
alert_key = recommendation-review-gap-{run_uid}
rule_key  = recommendation.review_gap
```

This creates one idempotent alert per unreviewed recommendation run.

## Clearing Rule

An open alert is resolved when the run no longer matches the trigger, usually because:

```text
at least one vkpi_recommendation_feedback row exists for that run
or the run is no longer previewed/completed
or the recommendation rows were removed
```

Resolved alerts remain in `vkpi_alerts` for audit history.

## Payload

The alert metadata stores:

```json
{
  "run_id": 518,
  "run_uid": "p4nlm-d8091029270e3230",
  "strategy_version": "new_launch_match_v1",
  "status": "previewed",
  "candidate_count": 1012,
  "recommendation_count": 3,
  "recommendation_rows": 3,
  "feedback_rows": 0,
  "created_at": "2026-05-19T05:51:38Z",
  "completed_at": "2026-05-19T05:51:38Z",
  "min_age_hours": 1
}
```

## API Surface

No new endpoint is added.

The rule is included in:

```text
POST /api/admin/vkpi/alerts/generate
```

Response field:

```json
{
  "recommendation_review": {
    "rule_key": "recommendation.review_gap",
    "count": 1,
    "cleared_count": 0
  }
}
```

## Acceptance Gates

```text
1. backend/app/services/vkpi/alerts.py py_compile passes.
2. generate_recommendation_review_gap_alerts() runs on the local DB.
3. Current local data creates one default review-gap alert because three preview runs are newer than min_age_hours.
4. Alert rows are idempotent by alert_key.
5. vkpi_ai_cost_ledger count is unchanged.
6. No frontend build is required for this package.
```

## Current Local Finding

```text
recommendation_runs=4
recommendations=84
feedback_rows=0
```

With `min_age_hours=0`, all four runs are eligible. With the default 1-hour gate, only the older completed run is currently open.

## Verified Result

Default age gate:

```text
first_count=1
second_count=1
open_rows.rows=1
open_rows.distinct_keys=1
open_rows.danger_rows=1
open_rows.warning_rows=0
ai_cost_before=0
ai_cost_after=0
```

All-runs path plus default cleanup:

```text
min_age_0_count=4
open_after_min_age_0=4
default_count=1
default_cleared_count=3
open_after_default=1
```

The remaining open alert is the older completed run with 75 recommendations and no feedback.

Smoke cleanup removed the three resolved short-age preview alert rows, leaving only the default current-state open alert.

## Next

P7-4 should add a compact alert status report and close P7 as an incremental anomaly layer.
