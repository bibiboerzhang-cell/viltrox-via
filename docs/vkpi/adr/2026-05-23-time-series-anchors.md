# ADR: Standardize Time-Series Anchors Before Trend Detection

- Date: 2026-05-23
- Status: Accepted
- Scope: V-KPI P6.72

## Context

P6.71 defined "fire" metrics from deltas and confidence. Before implementing trend detection, V-KPI needs a contract for which existing tables can replay time series and which fields represent cumulative totals versus deltas.

## Decision

Use existing tables as read-only anchors first:

- official channel snapshots;
- official post snapshots and deltas;
- industry account snapshots;
- industry post snapshots;
- metric lineage runs and values;
- market signal events.

Growth and velocity require delta fields or at least two comparable snapshots. Event tables can support trend counts but not metric velocity.

## Consequences

P6.73 trend detection should start with anchors that have documented delta fields. Cumulative-only anchors must not generate growth claims from a single row.
