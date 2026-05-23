# ADR: Evidence Agent Organizes, It Does Not Invent

## Context

P7 agents must consume the evidence layer without weakening the P0-P6 data
trust gates. The system already has Intelligence Card and Evidence Summary
contracts that normalize traceable evidence from cached rows and deterministic
rules.

## Decision

P7.81 implements Evidence Agent v0 as a read-only organizer. It reads explicit
KOL IDs or the latest P6.77 weekly actions, builds existing KOL evidence
summaries, and emits traceable evidence chains.

It does not generate new facts, write rows, create alerts, enqueue tasks,
trigger sync, call providers, or call LLMs.

## Rationale

Recommendation and brief agents need clean evidence packets, but those packets
must remain auditable. Extractive claims are allowed only when they keep concrete
`evidence_refs`.

## Consequences

- P7.82 Recommendation Agent can consume evidence chains instead of raw scattered
  rows.
- Missing sections stay missing and must be fixed through data-trust workflows.
- Any future LLM summary must use this chain as source material, not as permission
  to create unsupported facts.
