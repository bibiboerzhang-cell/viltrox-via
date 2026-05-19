# V-KPI P7-2 Content Brain Backlog Alert

## Scope

P7-2 adds one materialized-data anomaly rule:

```text
content_brain.analysis_backlog
```

It reads only:

```text
vkpi_industry_posts.analysis_status
```

It reuses:

```text
vkpi_alerts
backend/app/services/vkpi/alerts.py
```

It does not:

```text
call provider APIs
run content brain analysis
fetch platform posts
write vkpi_ai_cost_ledger
create a new alert table
create frontend UI
```

## Trigger Logic

The rule computes:

```text
post_count      = all vkpi_industry_posts rows
done_count      = rows where analysis_status='done'
pending_count   = post_count - done_count
coverage_ratio  = done_count / post_count
```

Default trigger:

```text
pending_count >= 5
or coverage_ratio < 0.80
```

Severity:

```text
danger  if pending_count >= 10 or coverage_ratio < 0.25
warning otherwise
```

Alert identity:

```text
alert_key = content-brain-analysis-backlog
rule_key  = content_brain.analysis_backlog
```

This makes the rule idempotent. Re-running the rule updates one alert row.

## Clearing Rule

The open alert is resolved when both are true:

```text
pending_count < 5
coverage_ratio >= 0.80
```

If `vkpi_industry_posts` is missing, the rule resolves any existing open backlog alert and returns `schema_ready=false`.

## Payload

The alert metadata stores:

```json
{
  "post_count": 10,
  "done_count": 3,
  "pending_count": 7,
  "coverage_ratio": 0.3,
  "status_counts": {
    "done": 3,
    "pending": 7
  },
  "min_pending": 5,
  "coverage_warning": 0.8,
  "oldest_pending_at": "2026-01-15T12:00:00Z",
  "newest_pending_at": "2026-04-23T09:08:02Z"
}
```

## API Surface

No new endpoint is added.

The rule is included in the existing aggregate generator:

```text
POST /api/admin/vkpi/alerts/generate
```

Response field:

```json
{
  "content_brain": {
    "rule_key": "content_brain.analysis_backlog",
    "count": 1,
    "pending_count": 7,
    "coverage_ratio": 0.3
  }
}
```

## Acceptance Gates

```text
1. backend/app/services/vkpi/alerts.py py_compile passes.
2. generate_content_brain_backlog_alerts() runs on the local DB.
3. Current P6 data creates one warning alert: 7 pending / 10 posts / coverage 0.30.
4. The alert is idempotent by alert_key.
5. With high thresholds, the rule can return count=0 without provider calls.
6. vkpi_ai_cost_ledger count is unchanged.
7. No frontend build is required for this package.
```

## Verified Result

Local smoke:

```text
quiet_count=0
active_after_quiet=0
triggered_count=1
pending_count=7
coverage_ratio=0.3
alert_row.status=open
alert_row.severity=warning
alert_row.rule_key=content_brain.analysis_backlog
alert_row.target_type=content_brain
ai_cost_before=0
ai_cost_after=0
```

The open alert is a real current-state alert for the P6 backlog, not test residue.

## Next

P7-3 can add one more rule from existing materialized data, preferably a recommendation-review or project-next-action freshness rule.
