# ADR: Define Fire From Deltas And Confidence

- Date: 2026-05-23
- Status: Accepted
- Scope: V-KPI P6.71

## Context

The next brain layer needs to identify what is becoming hot. The data trust phase already showed why cumulative totals, sample narrowing, declared comments, and latest-only refreshes can mislead.

## Decision

Define "fire" from true deltas and confidence-gated evidence:

- views velocity;
- engagement velocity;
- growth acceleration;
- comment quality signal;
- conversion proxy;
- cross-platform spread.

Do not use LLM summaries, cumulative latest totals, or missing comment bodies as source facts.

## Consequences

Trend detection in P6.73 must use these definitions. Forecasting and calibration cannot start until time-series anchors and post-level deltas are available.
