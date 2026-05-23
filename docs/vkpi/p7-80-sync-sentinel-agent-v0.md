# V-KPI P7.80 Sync Sentinel Agent v0

P7.80 adds the first P7 agent-shaped report: Sync Sentinel v0.

It is read-only. It does not run as a background worker, write alerts,
acknowledge sync guards, mutate budgets, trigger sync jobs, enqueue tasks, call
providers, or call LLMs.

## Inputs

Sync Sentinel reads existing state:

- `/api/admin/vkpi/sync/overview` service data
- `vkpi_sync_runs` guard state through `sync_status`
- `vkpi_provider_budget_caps`
- open rows from `vkpi_alerts`
- latest P6.79 brain-layer acceptance artifact in `runtime/ops`

## Output

The report returns:

- `sentinel_status`: `healthy`, `degraded`, or `blocked`
- sync guard and failure-rate status
- budget warning and hard-stop counts
- existing open alert count
- prioritized signals with evidence and recommended human action

Signal categories include:

- `sync_guard`
- `sync_failure_rate`
- `budget`
- `existing_alert`
- `calibration`
- `business_confirmation`
- `brain_layer`

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_sync_sentinel_agent_v0.py \
  --ops-dir runtime/ops \
  --limit 50 \
  --json-out runtime/ops/p7-80-sync-sentinel-agent-v0.json \
  --md-out runtime/ops/p7-80-sync-sentinel-agent-v0.md
```

The report passes when:

- sync overview is readable
- daily sync guard state is visible
- budget snapshot is readable
- open alert snapshot is readable
- latest P6.79 artifact is present
- all side-effect flags stay false

Open warnings or critical signals do not fail the report. They change
`sentinel_status` and require operator review.

## API

```http
GET /api/admin/vkpi/sync/sentinel/v0?limit=50
```

The endpoint is read-only and uses the same service as the CLI.
