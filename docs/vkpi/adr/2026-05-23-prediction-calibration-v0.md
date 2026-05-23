# ADR: Calibrate Launch Estimates Before Tuning

## Context

P6.74 creates a rule-based launch acceptance estimate. Before changing weights
or adding models, V-KPI needs a visible prediction-versus-truth loop.

## Decision

P6.75 reads saved P6.74 artifacts and compares them with current trend truth
proxies from P6.73. Same-day comparisons are allowed only as smoke checks.
Official accuracy requires a prediction generated before the truth day.

## Rationale

The first calibration target is not sales conversion. It is whether the
shortlisted platforms overlap with observed abnormal growth signals. This keeps
the metric available now while clearly labeling it as a proxy.

## Consequences

- Same-day smoke output must not be used to tune estimator weights.
- Tomorrow's run can become official once a cross-day prediction exists.
- Model training remains out of scope.
