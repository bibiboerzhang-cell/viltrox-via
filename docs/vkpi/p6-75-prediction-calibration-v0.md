# V-KPI P6.75 Prediction Calibration v0

P6.75 adds a read-only calibration report for P6.74 estimates. It compares a
saved new-launch acceptance artifact with current trend truth proxies.

Same-day comparisons are labeled as smoke checks. Official accuracy requires a
prediction artifact generated before the truth day.

## Inputs

- P6.74 artifact: `*p6-74-new-launch-acceptance-v0.json`
- P6.73 trend truth proxy: generated live or supplied as JSON

## Metrics

- `proxy_precision`: predicted Top N candidates whose platform had abnormal
  growth in the truth window.
- `proxy_platform_coverage`: predicted platforms that overlap with abnormal
  growth platforms.
- `accuracy_official`: true only when the prediction day is earlier than the
  truth day.

These are proxy metrics, not sales conversion accuracy.

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_prediction_calibration_v0.py \
  --ops-dir runtime/ops \
  --top-n 12 \
  --json-out runtime/ops/p6-75-prediction-calibration-v0.json \
  --md-out runtime/ops/p6-75-prediction-calibration-v0.md
```

The report passes when:

- a P6.74 prediction artifact is loaded
- trend truth is loaded
- candidate rows and truth platforms are available
- official versus smoke status is explicit
- provider, LLM, write, task, and sync flags stay false

## API

```http
GET /api/admin/vkpi/industry-data/prediction-calibration/v0?top_n=12
```

The endpoint is read-only and uses the latest P6.74 artifact by default.
