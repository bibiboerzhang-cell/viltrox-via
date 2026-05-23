# V-KPI P6.72 Time-Series Anchors

P6.72 standardizes the existing snapshot and metric tables that can be used to replay trends.

## Contract

- `snapshot_date` is the business date of the snapshot.
- `captured_at` is the actual collection timestamp.
- `created_at` is acceptable for event-like records.
- `generated_at` is used for metric lineage runs.
- Cumulative totals are not growth unless compared to an earlier snapshot.
- Delta fields can be used for velocity only when their method is documented.
- Market signals are event anchors, not continuous metric deltas.

## Anchors

- `official_channel_daily`: `vkpi_channel_metrics`
- `official_post_daily`: `vkpi_channel_post_metrics`
- `industry_account_daily`: `vkpi_industry_account_snapshots`
- `industry_post_daily`: `vkpi_industry_post_metrics`
- `metric_lineage_run`: `vkpi_metric_runs`
- `metric_lineage_value`: `vkpi_metric_values`
- `market_signal_event`: `vkpi_competitor_signals`

## CLI

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_time_series_anchors.py \
  --json-out runtime/ops/p6-72-time-series-anchors.json \
  --md-out runtime/ops/p6-72-time-series-anchors.md \
  --json
```

## Acceptance

- `provider_calls=false`
- `llm_calls=false`
- `write_db=false`
- `sync_triggered=false`
- anchors classify time fields, entity keys, cumulative fields, and delta fields
- report shows which anchors are currently trend-replay ready
