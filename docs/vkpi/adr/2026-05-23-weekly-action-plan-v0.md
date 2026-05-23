# ADR: Weekly Planning Starts as a Read-Only Checklist

## Context

P6.76 gives the daily new-signal digest. The next layer should answer "what
should we review this week" without turning into automatic outreach or opaque
recommendations.

## Decision

P6.77 creates a read-only weekly action plan from:

- KOL candidates in P6.74
- growth and comment review prompts in P6.76
- market signal clusters when present

The output is a checklist for humans. It does not create decisions, projects,
tasks, messages, or outreach.

## Rationale

This keeps the business loop actionable while preserving evidence review and
human approval before any decision layer consumes the output.

## Consequences

- Actions can seed later planning UI.
- P6.78 can evaluate whether completed actions improved outcomes.
- This remains separate from the recommendation engine.
