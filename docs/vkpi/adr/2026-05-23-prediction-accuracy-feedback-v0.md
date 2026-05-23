# ADR: Prediction Feedback Requires Official Cross-Day Runs

## Context

P6.75 can compare a P6.74 prediction with current trend truth proxies. Same-day
comparisons are useful smoke checks, but they do not prove prediction accuracy.
P6.78 needs to make that distinction visible before any estimator tuning is
considered.

## Decision

P6.78 reads P6.75 JSON artifacts from `runtime/ops`, splits them into official
cross-day runs and same-day smoke runs, and reports precision/coverage trends by
SKU, risk tier, and report date.

`calibration_allowed` only becomes true after the configured minimum official
run count is met. Automatic tuning and weight updates remain disabled in every
case.

## Rationale

This prevents same-day smoke results from being mistaken for model accuracy and
keeps the "brain layer" auditable before it can influence recommendations.

## Consequences

- Current same-day data can verify wiring but cannot tune weights.
- Cross-day P6.75 artifacts become the minimum evidence for calibration review.
- Any later model update needs a separate human-reviewed change path.
