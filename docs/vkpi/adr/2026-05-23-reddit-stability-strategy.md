# ADR: Reddit Is Best-Effort Unless OAuth Is Proven

Date: 2026-05-23

## Decision

V-KPI will treat Reddit as a bounded, best-effort market-signal source. It will
not promise full Reddit coverage.

The preferred path is OAuth/PRAW. Public JSON can be used only for allowlisted
subreddits and selected post comments. Apify is a fallback behind budget
approval.

## Rationale

Reddit data access is operationally unstable: OAuth setup, public JSON
availability, subreddit rules, moderation, deleted comments, nested comments,
and rate limiting all affect completeness. Marketing decisions can use Reddit
signals only when evidence carries clear source and completeness status.

## Consequences

- No broad all-Reddit search.
- No daily Reddit collection until a watchlist is approved.
- Comments require selected post IDs.
- Public JSON smoke tests must be explicitly gated to avoid accidental network
  calls during offline verification.
- Apify fallback requires budget approval.

## Follow-Up

P5.68 will separately evaluate X comments with a limited 14-target go/no-go.
