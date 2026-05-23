# V-KPI P6.71 Fire Metric Definitions

P6.71 defines what "火" means before trend detection, forecasting, calibration, or model work starts.

## Core Rule

Do not call a creator, post, SKU, or market signal "hot" from cumulative totals alone. A hot signal needs true deltas, source freshness, and confidence.

## Metrics

- `views_velocity`: post view delta per hour.
- `engagement_velocity`: likes, comments, shares delta per hour.
- `growth_acceleration`: current velocity versus previous velocity.
- `comment_quality_signal`: useful intent, complaint, question, product-fit, and competitor comments.
- `conversion_proxy`: valid clicks, attribution, coupon/tag evidence, and project progress.
- `cross_platform_spread`: related signals across platforms weighted by freshness and confidence.

## Confidence Rules

- `baseline_protected` lowers confidence instead of displaying misleading `+0`.
- Declared comments without cached bodies cannot count as comment quality.
- LLM summaries can explain existing evidence but cannot create source facts.
- Official low-frequency full-scope refresh and latest-30 creator refresh must not share the same baseline semantics.

## CLI

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_fire_metric_definitions.py \
  --json-out runtime/ops/p6-71-fire-metric-definitions.json \
  --md-out runtime/ops/p6-71-fire-metric-definitions.md \
  --json
```

## Acceptance

- `provider_calls=false`
- `llm_calls=false`
- `write_db=false`
- `sync_triggered=false`
- all metrics have formula, source, fallback, and direction
