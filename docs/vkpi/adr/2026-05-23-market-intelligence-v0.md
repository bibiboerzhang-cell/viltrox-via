# ADR: Market Intelligence v0 Uses Existing Reviewed Signals First

- Date: 2026-05-23
- Status: Accepted
- Scope: V-KPI P5.69

## Context

V-KPI needs a first market intelligence surface for competitor launches, hot topics, and comment opportunities. Source expansion has separate gates: Reddit P5.67, X P5.68, and RSS/site watch under P5.69+.

## Decision

Market Intelligence v0 reads only existing V-KPI signal tables, starting with `vkpi_competitor_signals`.

It does not:

- fetch RSS, websites, Reddit, X, or YouTube;
- call Apify, Gemini, or LLM providers;
- enqueue sync work;
- write canonical market facts automatically.

## Consequences

The first UI is immediately useful because it summarizes already-reviewed or pending signals. It is also intentionally incomplete. New market sources must pass their own source gates before they can feed this panel.
