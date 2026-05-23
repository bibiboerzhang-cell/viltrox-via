# ADR: Market Signal Source Design Before Crawling

Date: 2026-05-23

## Decision

V-KPI will not begin broad market crawling from Reddit, X, RSS feeds, competitor
sites, or YouTube search until each source has a bounded gate, canonical
contract, and reviewed storage path.

P5.66 is design-only. It adds a source registry and readiness report, not a
crawler.

## Rationale

Market sources have different cost, legal, operational, and data-quality risk.
Treating them as one generic crawler would make provider cost and evidence
quality hard to audit.

The safe sequence is:

1. define source contract and storage direction
2. verify table readiness and gates
3. run source-specific go/no-go spikes
4. only then collect limited reviewed signals

## Consequences

- Reddit moves to P5.67 for OAuth/best-effort strategy.
- X comments move to P5.68 for a 14-target go/no-go.
- RSS and competitor official site watch move to P5.69 with allowlists.
- No source can write directly into Product Fit or recommendation ranking.

## Non-Goals

- no external crawling
- no provider execution
- no LLM summarization
- no automatic alerting
- no recommendation scoring changes
