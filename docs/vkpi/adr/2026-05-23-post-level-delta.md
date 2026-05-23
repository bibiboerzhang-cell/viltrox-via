# ADR: Official Channel Post-Level Delta Semantics

Decision date: 2026-05-23

## Context

Official-account refreshes can return a narrower post sample than the historical baseline. When a provider returns only the latest N posts, account-level cumulative totals may stay protected by `cumulative_floor`; however, user-facing growth still needs an honest way to show real new activity.

The existing implementation already records per-post snapshots in `vkpi_channel_post_metrics` through `record_channel_post_metrics()` and migration `073_vkpi_channel_post_metrics.sql`. This ADR freezes the semantics before the next dry run/backfill work.

## Decision

Use post-level deltas as the preferred evidence for real content growth when account-level cumulative totals are protected.

Canonical row identity:

- `channel_id`
- `platform`
- `post_uid`
- `snapshot_date`

Metric columns:

- cumulative post metrics: `views`, `likes`, `comments`, `shares`
- computed deltas: `views_delta`, `likes_delta`, `comments_delta`, `shares_delta`
- method marker: `delta_method = post_metric_delta_v1`
- provenance: `raw_post_json`, `first_seen_at`, `captured_at`

## Delta Rules

For a post already seen in a prior snapshot:

```text
metric_delta = max(0, current_post_metric - previous_post_metric)
```

For a post first seen in the current snapshot:

- If `posted_at > previous_channel_snapshot.captured_at`, treat it as a new post and count its current metrics as new activity.
- If `posted_at <= previous_channel_snapshot.captured_at` or `posted_at` is unknown, mark it as `first_seen_existing_posts` and use zero delta, avoiding a fake spike caused by backfilling older content.

For account-level official-channel deltas:

```text
views_delta = max(account_cumulative_delta, sum(post.views_delta))
likes_delta = max(account_cumulative_delta, sum(post.likes_delta))
posts_delta = max(account_posts_delta, post_level_new_posts)
```

Negative deltas are not treated as negative growth. They mean deleted content, hidden metrics, provider sample narrowing, or provider inconsistency. Those cases stay visible through `baseline_protected` and raw provenance rather than being displayed as real decline.

## Platform Notes

- YouTube: use stable video ID where available.
- Instagram: prefer `shortCode`, then provider `id`, then URL.
- TikTok: prefer video `id`, then `webVideoUrl`.
- Facebook: prefer `postId`, then provider `id`, then URL.
- Reddit: views are usually unavailable; score/comments can still produce post-level engagement deltas.
- X: prefer tweet `id`, then URL; impressions/views are provider-dependent and must remain provenance-backed.

## Non-Goals

This ADR does not define comment-body completeness. Comment availability will be covered by the separate `declared / cached / cap / status` comment contract.

This ADR does not authorize broad historical deep scans. Backfill must start with the 18 official accounts only and must not trigger the 1012 KOL deep scan.

This ADR does not authorize using Apify batch optimization for legacy full-pool refresh. That remains gated by P1.X.A selector work.

## Acceptance

- `vkpi_channel_post_metrics` exists in migrations and schema guards.
- `record_channel_post_metrics()` writes per-post cumulative metrics and positive deltas.
- Official-channel account deltas can explain real new activity even when `baseline_protected` is true.
- First-seen older posts do not create artificial growth.
- The 18 official-account dry run reports `matched_posts`, `new_posts`, `first_seen_existing_posts`, and metric deltas.
