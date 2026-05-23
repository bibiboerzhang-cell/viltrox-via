# ADR: Brief Agent Is A Read-Only Checklist

## Context

P7 agents should make operator review faster without crossing into autonomous
action. P7.81 organizes evidence and P7.82 proposes candidates. The next useful
layer is a compact brief, but only if it remains evidence-linked.

## Decision

P7.83 implements Brief Agent v0 as a read-only checklist generator. It reads
P7.82 Recommendation Agent output, emits evidence-linked brief items and human
review actions, and keeps all side effects disabled.

## Rationale

Operators need a concise view of candidates, risks, and gaps. That view must not
invent facts or create work automatically. Every item keeps evidence refs, and
untraceable content is dropped or blocked.

## Consequences

- Daily/operator briefs can be rendered from one report artifact.
- Human review remains the decision boundary.
- Future notification or daily digest delivery must consume this output only
  after adding an explicit write/approval boundary.
