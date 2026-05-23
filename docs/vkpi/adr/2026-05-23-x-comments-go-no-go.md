# ADR: X Comments Go/No-Go Gate

- Date: 2026-05-23
- Status: Accepted
- Scope: V-KPI P5.68

## Context

X comments may contain useful competitor, launch, and creator conversation signal, but X is a high-risk and potentially high-cost source. The system must not start daily X collection or broad X crawling from a planning task.

## Decision

Use a 14-target validation gate before any continuation.

- The default P5.68 implementation is read-only.
- A provider run requires explicit approval, exactly 14 selected targets, and a configured comments provider.
- Official X API replies are preferred only when `X_BEARER_TOKEN` is approved.
- Apify comments are allowed only when `APIFY_X_COMMENTS_ACTOR_ID` is explicitly configured behind budget approval.
- Results must first become validation artifacts. They do not automatically enter recommendation or market tables.

## Stop Rules

- Stop at provider errors `>= 3`.
- Stop if cost or rate limit exceeds the approved validation budget.
- Retry a failed target at most once.
- Do not promote to daily X collection from validation results alone.

## Consequences

This keeps P5.68 compatible with the current no-bulk-provider policy. It also gives a concrete go/no-go shape for a future manually approved validation run without creating a hidden crawler.
