# ADR: Start Trend Detection With Rules and True Deltas

## Context

P6.71 defined what "fire" means, and P6.72 standardized the time-series anchors.
The next step is to detect unusual growth without overclaiming from incomplete
data.

## Decision

P6.73 uses rule-based trend detection first:

- official post growth comes from `vkpi_channel_post_metrics` delta fields
- official channel movement comes from `vkpi_channel_metrics` daily delta fields
- market signals come from event counts in `vkpi_competitor_signals`
- cumulative-only latest totals are not growth evidence
- event bursts are not treated as metric deltas

The first version exposes a read-only service, CLI, and admin API endpoint. It
does not write detection results yet.

## Rationale

This keeps the growth layer explainable before forecasting and calibration. A
watch signal can be useful, but it must stay separate from abnormal growth. This
also prevents the system from calling old cold data, baseline-protected totals,
or one-off market events a trend.

## Consequences

- P6.74 can consume abnormal growth signals as candidate inputs.
- P6.75 can compare detected signals with next-day truth before calibration.
- No model training starts from this ADR.
