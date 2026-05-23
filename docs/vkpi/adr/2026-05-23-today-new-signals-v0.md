# ADR: Daily New Signals Are a Read-Only Review Digest

## Context

P6.73 detects trend signals, P6.74 estimates launch acceptance, and P6.75 starts
calibration. Operators still need a compact "what changed in the last 24h"
surface before weekly planning.

## Decision

P6.76 creates a read-only daily signal digest:

- official growth from P6.73 trend signals
- competitor and market events from existing signal records
- comment anomalies from cached comment bodies only
- action items generated as review prompts, not automatic decisions

## Rationale

This gives the team a daily operating loop without adding crawlers, sync load,
LLM summaries, or premature recommendation automation.

## Consequences

- Missing comments are visible as missing cached evidence.
- The digest can seed P6.77 "this week" recommendations later.
- No provider budget or sync path changes are introduced.
