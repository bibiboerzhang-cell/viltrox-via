# ADR: Recommendation Agent Proposes, Humans Decide

## Context

P7 introduces agent-shaped helpers after sync governance, data-trust work, and
evidence organization. The system already has persisted recommendation tables,
but autonomous writes would bypass the review loop and weaken traceability.

## Decision

P7.82 implements Recommendation Agent v0 as a read-only candidate planner. It
reads P7.81 evidence chains, existing competitor relation rows, and existing
operator feedback. It ranks candidates and returns suggested decision buckets,
but it does not write recommendation rows or trigger any follow-up action.

## Rationale

The agent should make the next human review easier, not replace it. Every
candidate keeps evidence refs and `human_confirmation_required=true`. Candidates
with too little traceable evidence stay blocked.

## Consequences

- Recommendation review can start from a ranked candidate list.
- Existing recommendation tables remain the persistence boundary.
- Missing evidence must be fixed through P1/P2 data-trust flows, not by agent
  inference.
- Future automation can reuse this output only after adding an explicit human
  approval step.
