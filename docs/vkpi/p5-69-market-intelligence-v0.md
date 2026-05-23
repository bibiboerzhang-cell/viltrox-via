# V-KPI P5.69 Market Intelligence v0

P5.69 exposes a small read-only market intelligence surface from existing V-KPI signal tables. It does not start RSS, Reddit, X, YouTube search, Apify, Gemini, or LLM collection.

## Scope

- Source: `vkpi_competitor_signals`.
- Output: hot brands, hot topics, competitor launch candidates, comment opportunities, and high-priority review items.
- UI: Data Analysis page panel using the read-only API.
- No writes, no provider calls, no sync trigger.

## API

```text
GET /api/admin/vkpi/industry-data/market-intelligence/v0?limit=120
```

The payload includes:

- `summary`
- `hot_brands`
- `hot_topics`
- `launch_candidates`
- `comment_opportunities`
- `high_priority`
- `checks`
- `policy`

## CLI

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_market_intelligence_v0.py \
  --json-out runtime/ops/p5-69-market-intelligence-v0.json \
  --md-out runtime/ops/p5-69-market-intelligence-v0.md \
  --json
```

## Acceptance

- `provider_calls=false`
- `external_http_calls=false`
- `write_db=false`
- `sync_triggered=false`
- Data comes from `vkpi_competitor_signals`
- Data Analysis page can show the panel without launching a crawler
