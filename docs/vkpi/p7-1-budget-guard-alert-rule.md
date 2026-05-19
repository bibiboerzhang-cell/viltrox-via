# V-KPI P7-1 Budget Guard Alert Rule

## Scope

P7-1 adds the first anomaly rule to the existing alert system:

```text
budget_guard.warning_or_hard_stop
```

It reuses:

```text
vkpi_provider_budget_caps
vkpi_alerts
backend/app/services/vkpi/alerts.py
```

It does not:

```text
create a new alert table
call LLM providers
write vkpi_ai_cost_ledger
change budget caps
create frontend UI
create cron scheduling
```

## Trigger Logic

For each row in `vkpi_provider_budget_caps`:

```text
usage_ratio = current_spend / cap_usd
warning     = cap_usd > 0 and usage_ratio >= warning_at
hard_stop   = cap_usd > 0 and usage_ratio >= hard_stop_at
```

Alert behavior:

```text
hard_stop=true  -> severity=danger
warning=true    -> severity=warning
otherwise       -> resolve existing open alert for that scope
```

Alert identity:

```text
alert_key = budget-guard-{scope}
rule_key  = budget_guard.warning_or_hard_stop
```

This keeps the rule idempotent. Re-running the rule updates the same alert row instead of creating duplicates.

## Alert Payload

Each open alert stores:

```json
{
  "scope": "cron:p4_new_launch_match",
  "cap_usd": 50.0,
  "current_spend": 42.0,
  "usage_ratio": 0.84,
  "warning_at": 0.8,
  "hard_stop_at": 1.0,
  "hard_stopped": false,
  "warning": true,
  "reset_at": null,
  "fallback_action": "dry_run_only"
}
```

The alert body is intentionally short and operator-facing:

```text
{scope} spend is ${current_spend} of ${cap_usd} ({usage_ratio}). Fallback action: {fallback_action}.
```

## Clearing Rule

If a scope falls below both thresholds, the existing open alert is marked:

```text
status=resolved
resolved_at=now
updated_at=now
```

Resolved alerts remain in `vkpi_alerts` for audit history.

## API Surface

No new endpoint is added in P7-1.

The rule is executed through the existing aggregate alert generator:

```text
POST /api/admin/vkpi/alerts/generate
```

The response now includes:

```json
{
  "budget_guard": {
    "rule_key": "budget_guard.warning_or_hard_stop",
    "count": 0,
    "cleared_count": 0
  }
}
```

## Acceptance Gates

```text
1. backend/app/services/vkpi/alerts.py py_compile passes.
2. generate_budget_guard_alerts() runs on the local DB.
3. With current_spend=0, no budget alert is created.
4. Existing open budget alerts are resolved when spend recovers below warning_at.
5. Alert rows are idempotent by alert_key.
6. vkpi_ai_cost_ledger count is unchanged.
7. No frontend build is required for this package.
```

## Verified Result

Local smoke covered both paths:

```text
current production caps:
  rule_key=budget_guard.warning_or_hard_stop
  count=0
  cleared_count=0
  budget_alert_rows=0
  ai_cost_before=0
  ai_cost_after=0

temporary warning simulation:
  created_count=1
  created_row.status=open
  created_row.severity=warning
  cleared_count=1
  cleared_row.status=resolved
  cleanup_alert_rows=0
  cleanup_cap_rows=0
```

## Next

P7-2 should add a second rule from an already materialized source table.

Do not introduce provider calls, crawler calls, or a parallel alert store in P7.
