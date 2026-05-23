# ADR: Brain Layer v0 Acceptance Is Technical, Not Business Sign-Off

## Context

P6.71-P6.78 created the rule-based brain layer: fire definitions, time-series
anchors, trend detection, launch acceptance, calibration smoke checks, daily
signals, weekly action planning, and prediction feedback.

The last P6 step needs to verify whether this layer can support human planning
without pretending that same-day smoke checks are official accuracy.

## Decision

P6.79 aggregates the latest P6.71-P6.78 artifacts from `runtime/ops` into a
read-only technical acceptance report.

The report can mark technical acceptance passed while keeping
`business_confirmed=false`. Business confirmation remains a separate human
decision. Auto tuning, weight updates, provider calls, LLM calls, sync, and task
creation remain blocked.

## Rationale

This keeps the brain layer useful for review/contact planning while preserving
the gates that were built in P0-P6: evidence first, calibration before tuning,
and human approval before decision automation.

## Consequences

- P6 can close technically without claiming business adoption.
- P7 agents must consume this as a guarded evidence layer, not as permission for
  autonomous outreach or model updates.
- Cross-day P6.75 artifacts remain required before any prediction tuning.
