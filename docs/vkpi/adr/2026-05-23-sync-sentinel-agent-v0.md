# ADR: Sync Sentinel Starts as a Read-Only Agent

## Context

P7 introduces agents, but P0 sync governance remains the safety boundary.
The first agent must not create a second control plane for sync, budget, or
alerts.

## Decision

P7.80 implements Sync Sentinel v0 as a read-only report over existing state:

- sync overview and guard state
- provider budget caps
- existing open alerts
- P6.79 brain-layer acceptance

It emits prioritized signals and recommended human actions, but it does not
write alert rows, acknowledge guards, trigger sync, mutate budgets, enqueue
tasks, or call AI providers.

## Rationale

This gives operators one place to check sync risk without weakening the
backup-first and no-overlap rules. It also keeps the P7 agent layer subordinate
to evidence and audit controls.

## Consequences

- Sync Sentinel can be deployed before autonomous agents.
- Real automation still requires a later audit table and explicit action gates.
- Any guard ack remains a separate manual operation with an explicit reason.
