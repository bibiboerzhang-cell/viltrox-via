# V-KPI P6.76 Today New Signals v0

P6.76 adds a read-only 24h signal digest. It combines existing trend detection,
competitor signal records, and cached comment records into an action-oriented
daily review payload.

It does not fetch providers, call LLMs, write the database, enqueue tasks, or
trigger sync.

## Inputs

- P6.73 trend detection v0 for official content growth.
- `vkpi_competitor_signals` for market and competitor events.
- `vkpi_comments` plus optional `vkpi_sentiment_results` for cached comment
  anomalies.

## Comment Honesty

Comments are treated as a cached window only:

- `cached=0` means no cached comment evidence.
- cached comments without sentiment are labeled `cached_without_sentiment`.
- declared platform counts are not treated as comment body evidence.

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_today_new_signals_v0.py \
  --lookback-hours 24 \
  --limit 100 \
  --json-out runtime/ops/p6-76-today-new-signals-v0.json \
  --md-out runtime/ops/p6-76-today-new-signals-v0.md
```

The report passes when:

- trend detection loads
- source pipelines are available, even when the current 24h window has no new
  actionable signal
- comment contract is present
- action items are generated
- provider, LLM, write, task, and sync flags stay false

## API

```http
GET /api/admin/vkpi/industry-data/today-new-signals/v0?lookback_hours=24&limit=100
```

The endpoint is read-only and uses the same service as the CLI.
