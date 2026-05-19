# Post-v5.3.1 Operating Review

## Scope

This package is a read-only operating snapshot for the first review cycle after v5.3.1 completion.

It does not add schema, write database rows, call providers, or change recommendation behavior. Its only job is to show the current operating backlog in one place before the next cleanup or automation package starts.

## Inputs

- `vkpi_alerts`: open alert backlog from P7.
- `vkpi_competitor_signals`: pending review competitor signals from P8.
- `vkpi_kol_recommendation_runs` and `vkpi_recommendation_feedback`: recommendation runs that have no human feedback yet.
- `vkpi_recommendation_outcomes`: current action/outcome volume.
- `vkpi_memory_feedback`: Memory feedback adoption gap from P3.5/P10.

Missing optional tables are treated as zero rows so the snapshot can run across local and production-like environments.

## CLI

```bash
.venv/bin/python scripts/vkpi_operating_review.py
.venv/bin/python scripts/vkpi_operating_review.py --json
.venv/bin/python scripts/vkpi_operating_review.py --json-out /tmp/vkpi-operating-review.json --md-out /tmp/vkpi-operating-review.md
```

Default output is Markdown. `--json` prints the same payload without the generated Markdown string.

## API

```text
GET /api/admin/vkpi/operating-review/status?limit=25
```

Access is gated by `require_tab("vkpi", "read")`.

## Frontend Entry

Managers can read the same snapshot from the System Settings page through the `Operating Review` panel.

The panel is intentionally read-only. It shows:

- Open alert count.
- Pending competitor signal count.
- Recommendation and Memory feedback gaps.
- Current `provider_calls` and `write_db` safety flags.
- Top work items with source table and source id.

## Output Contract

```json
{
  "scenario": "vkpi_operating_review",
  "provider_calls": false,
  "write_db": false,
  "counts": {
    "open_alerts": 18,
    "competitor_signals": 25,
    "pending_competitor_signals": 25,
    "recommendation_feedback": 0,
    "recommendation_outcomes": 171,
    "memory_feedback": 0
  },
  "top_work_items": [],
  "gaps": [
    "open_alerts_need_resolution",
    "competitor_signals_pending_review",
    "recommendation_feedback_empty",
    "memory_feedback_empty"
  ]
}
```

The exact counts can drift as the team reviews alerts and competitor signals. The invariant is that `provider_calls=false` and `write_db=false`.

## Review Use

The snapshot is meant to answer three questions quickly:

1. Which open alerts need human resolution first?
2. Which competitor signals are still pending review?
3. Are recommendation and Memory feedback loops still empty?

Do not use this endpoint as a scheduler. P12 task allocation and future automation packages should consume the underlying tables directly after their own acceptance gates.

## Acceptance

- CLI runs without DB writes.
- API route is registered under `/api/admin/vkpi`.
- `provider_calls=false`.
- `write_db=false`.
- Open alert, competitor review, and feedback-gap counts are visible in one payload.
