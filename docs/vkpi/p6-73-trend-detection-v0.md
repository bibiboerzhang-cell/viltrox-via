# V-KPI P6.73 Trend Detection v0

P6.73 adds read-only, rule-based trend detection. It does not train a model, call
LLMs, call providers, enqueue jobs, write the database, or trigger sync.

## Inputs

- `vkpi_channel_post_metrics`: official post deltas and snapshot history.
- `vkpi_channel_metrics`: official channel daily deltas.
- `vkpi_competitor_signals`: market signal events.

## Rules

- `official_post_views_delta_spike`
- `official_post_engagement_delta_spike`
- `official_post_growth_acceleration`
- `official_channel_daily_delta_watch`
- `market_signal_event_burst`

The rules only call something metric growth when delta fields or comparable
multi-snapshot history exist. Cumulative latest totals are not growth evidence.
Market bursts are labeled as event signals, not metric deltas.

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_trend_detection_v0.py \
  --lookback-days 14 \
  --limit 5000 \
  --top-signals 25 \
  --json-out runtime/ops/p6-73-trend-detection-v0.json \
  --md-out runtime/ops/p6-73-trend-detection-v0.md
```

The report passes when:

- rule definitions are loaded
- delta-based growth guards are active
- event signals stay separate from growth deltas
- at least one existing anchor has data
- provider, LLM, write, task, and sync flags are all false

## API

```http
GET /api/admin/vkpi/industry-data/trend-detection/v0?lookback_days=14&limit=5000&top_signals=25
```

The endpoint is read-only and uses the same service as the CLI.
