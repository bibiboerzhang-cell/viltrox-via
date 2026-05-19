# V-KPI P7 Alerts Completion Report

## Status

P7 is complete as an incremental anomaly layer on top of existing `vkpi_alerts`.

## Completed Packages

```text
P7-1  budget guard warning / hard-stop alert rule
P7-2  content brain analysis backlog alert rule
P7-3  recommendation review gap alert rule
P7-4  read-only alert status report CLI
P7-5  post-v5.3.1 alert triage suggestions
```

## Files

```text
backend/app/services/vkpi/alerts.py
backend/app/api/routers/vkpi_attribution_metrics.py
scripts/p7_alert_status.py
docs/vkpi/p7-1-budget-guard-alert-rule.md
docs/vkpi/p7-2-content-brain-backlog-alert.md
docs/vkpi/p7-3-recommendation-review-gap-alert.md
docs/vkpi/p7-alerts-completion-report.md
```

## Current Alert State

Read-only report:

```bash
python3 scripts/p7_alert_status.py --limit 10
```

Current output:

```text
open_total=2
p7_open_total=2
ai_cost_calls=0
ai_cost_spend=0.0000
budget_warning_scopes=0
budget_hard_stop_scopes=0
```

By rule:

```text
content_brain.analysis_backlog  open warning  count=1
recommendation.review_gap       open danger   count=1
project.stalled_review          resolved warning count=16
```

The two P7 open alerts are real current-state alerts:

```text
content_brain.analysis_backlog:
  7 of 10 industry posts are still pending content brain analysis.

recommendation.review_gap:
  completed run recrun-af0053af53b32e1a has 75 recommendations and 0 feedback rows.
```

## Post-v5.3.1 Triage

Triage suggestions are deterministic and dry-run by default:

```bash
python3 scripts/p7_alert_status.py --triage-suggestions --limit 100
python3 scripts/p7_alert_status.py --apply-suggestions --limit 100
python3 scripts/p7_alert_status.py --apply-suggestions --limit 100 --confirm
```

API:

```text
GET  /api/admin/vkpi/alerts/triage-suggestions
POST /api/admin/vkpi/alerts/triage-suggestions/apply
```

Current dry-run result:

```text
count=18
suggested.resolve=16
suggested.keep_open=2
resolve target: project.stalled_review smoke fixture alerts
keep_open targets: recommendation.review_gap, content_brain.analysis_backlog
provider_calls=false
write_db=false
```

The apply endpoint writes only when the request body includes `confirm=true`.

Post-apply verification on 2026-05-19:

```text
apply_suggestions.confirm=true
candidate_count=16
applied_count=16
error_count=0
provider_calls=false
open_total=2
project.stalled_review.resolved=16
remaining_open=content_brain.analysis_backlog,recommendation.review_gap
vkpi_ai_cost_ledger.count=0
```

## Guarantees

```text
No new alert table was created.
No provider call was introduced.
No crawler call was introduced.
No vkpi_ai_cost_ledger row was written.
All P7 rules are idempotent by alert_key.
Recovered states resolve existing open alerts instead of deleting them.
```

## Acceptance

```text
python3 -m py_compile backend/app/services/vkpi/alerts.py passed
python3 -m py_compile scripts/p7_alert_status.py passed
git diff --check passed
P7-1 create/clear smoke passed
P7-2 quiet/default smoke passed
P7-3 default/all-runs smoke passed
scripts/p7_alert_status.py --json passed
scripts/p7_alert_status.py --triage-suggestions --limit 100 passed
scripts/p7_alert_status.py --apply-suggestions --limit 100 passed as dry-run
```

## Next

P8 can start competitor brain v0.

Keep P8 deterministic first:

```text
read existing industry/content/risk data
derive competitor/product/topic signals
write preview output first
do not call providers in the first package
do not create another alert store
```
