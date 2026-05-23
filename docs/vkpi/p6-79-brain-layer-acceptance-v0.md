# V-KPI P6.79 Brain Layer Acceptance v0

P6.79 is a read-only technical acceptance report for the P6.71-P6.78 brain
layer. It answers whether the current rule-based layer can assist human
new-launch and KOL planning decisions.

It is not a business sign-off, recommendation engine, model trainer, outreach
tool, or sync job.

## Inputs

P6.79 reads the latest `runtime/ops` artifacts for:

- P6.71 fire metric definitions
- P6.72 time-series anchors
- P6.73 trend detection
- P6.74 new launch acceptance
- P6.75 prediction calibration
- P6.76 today new signals
- P6.77 weekly action plan
- P6.78 prediction accuracy feedback

## Output

The report includes:

- loaded/passed status for every required phase
- side-effect guard status for each artifact
- decision support level
- whether it can assist a new-launch/KOL planning decision
- whether official prediction accuracy is still pending
- explicit business confirmation requirement

`business_confirmed` is always false in this report because that is a human
decision, not a technical artifact.

## Acceptance

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_brain_layer_acceptance_v0.py \
  --ops-dir runtime/ops \
  --json-out runtime/ops/p6-79-brain-layer-acceptance-v0.json \
  --md-out runtime/ops/p6-79-brain-layer-acceptance-v0.md
```

The report passes when:

- all required P6.71-P6.78 artifacts are present
- all required artifacts passed their own checks
- all artifacts keep provider, LLM, write, task, and sync flags false
- decision inputs exist
- P6.78 blocks auto tuning and weight updates
- official accuracy pending is explicit when cross-day calibration is not ready

## API

```http
GET /api/admin/vkpi/industry-data/brain-layer-acceptance/v0
```

The endpoint is read-only and uses the same service as the CLI.
