# ADR: Comment Data Contract

Date: 2026-05-23

## Context

V-KPI post cards have two different comment facts:

- `declared`: the public comment count shown by the platform or crawler metadata.
- `cached`: comment bodies that V-KPI has actually cached and can display.

These are not interchangeable. A post can show 1,000 public comments while V-KPI has cached 0, 30, or a capped sample. The UI must not imply that cached bodies are complete unless the counts prove it.

## Decision

Channel post comment responses expose a stable `comment_contract` object:

```json
{
  "declared": 120,
  "cached": 30,
  "cap": 300,
  "status": "partial"
}
```

Backwards-compatible top-level aliases are also returned:

- `declared_count`
- `cached_count`
- `comment_cap`
- `coverage_status`

`comment_count` remains the cached body count for older callers.

## Status Values

- `complete`: cached bodies are at least the declared platform count.
- `partial`: some bodies are cached, but fewer than declared.
- `capped`: cached bodies reached the request cap while the platform declares more.
- `not_cached`: platform declares comments, but no bodies are cached.
- `none_declared`: platform declares no comments and no bodies are cached.
- `cached_without_declared`: bodies exist even though the platform did not provide a declared count.
- `not_supported`: the platform or current provider cannot collect comment bodies.
- `missing_post_id`: V-KPI cannot map the post to a stable external ID.

## Consequences

The UI displays all three numbers when available: cached body count, platform declared count, and cap. Comment collection can remain capped per platform or per request without misleading staff into thinking all comment bodies are present.

This ADR does not enable any new batch collection, Apify run, LLM analysis, or sentiment inference.
