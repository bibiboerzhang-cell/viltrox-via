# V-KPI P6.78 Prediction Accuracy Feedback v0

P6.78 aggregates saved P6.75 calibration artifacts into a read-only feedback
report. It separates official cross-day accuracy from same-day smoke checks and
does not tune weights or write model state.

## Inputs

- `runtime/ops/*p6-75-prediction-calibration-v0.json`
- P6.75 summary fields:
  - `accuracy_official`
  - `calibration_status`
  - `proxy_precision`
  - `proxy_platform_coverage`
  - `prediction_sku`
  - `prediction_market_risk_tier`

## Output

The report includes:

- artifact count
- official run count
- smoke run count
- average official proxy precision and coverage
- average smoke proxy precision and coverage
- buckets by SKU, risk tier, and report date
- `calibration_allowed`, which only becomes true after enough official runs
- `auto_tuning_allowed=false` and `weight_update_allowed=false`

Same-day smoke metrics verify wiring only. They are not official accuracy and
must not tune estimator weights.

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_prediction_accuracy_feedback_v0.py \
  --ops-dir runtime/ops \
  --limit 100 \
  --min-official-runs 3 \
  --json-out runtime/ops/p6-78-prediction-accuracy-feedback-v0.json \
  --md-out runtime/ops/p6-78-prediction-accuracy-feedback-v0.md
```

The report passes when:

- at least one P6.75 artifact is loaded
- official and smoke runs are counted separately
- same-day smoke is not counted as official accuracy
- tuning guard fields are present
- provider, LLM, write, task, and sync flags stay false

If no P6.75 artifact exists, the report returns
`feedback_status=no_calibration_artifacts` and fails acceptance.

## API

```http
GET /api/admin/vkpi/industry-data/prediction-accuracy-feedback/v0?limit=100&min_official_runs=3
```

The endpoint is read-only and uses the same service as the CLI.
