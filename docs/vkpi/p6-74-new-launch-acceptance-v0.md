# V-KPI P6.74 New Launch Acceptance v0

P6.74 adds a read-only rule estimate for new product launch acceptance. It
combines existing KOL product-fit evidence, current trend signals, and market
risk. It is not a trained model and it does not create projects, outreach, tasks,
or recommendation records.

## Inputs

- `product_campaign_card.build_product_campaign_card()`: selected SKU, KOL
  candidates, product fit evidence, and market risk.
- `trend_detection_v0.build_trend_detection_v0()`: official post/channel growth
  signals and market event bursts.

## Output

The report returns `top_candidates` with:

- `acceptance_score`
- `acceptance_tier`
- KOL identity and platform
- product-fit evidence
- platform momentum evidence
- market risk penalty

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_new_launch_acceptance_v0.py \
  --top-n 12 \
  --kol-limit 200 \
  --lookback-days 14 \
  --json-out runtime/ops/p6-74-new-launch-acceptance-v0.json \
  --md-out runtime/ops/p6-74-new-launch-acceptance-v0.md
```

The report passes when:

- product campaign card data loads
- trend detection data loads
- Top N candidates are generated
- each candidate includes evidence
- provider, LLM, write, task, outreach, project, and sync flags stay false

## API

```http
GET /api/admin/vkpi/industry-data/new-launch-acceptance/v0?sku=&kol_limit=200&top_n=12&lookback_days=14
```

The endpoint is read-only and uses the same service as the CLI.
