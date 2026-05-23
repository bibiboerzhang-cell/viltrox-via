# ADR: Canonical Post Identity

Date: 2026-05-23

## Context

V-KPI now uses official-channel posts across three data-trust paths:

- media rendering and cache lookup in `channels.py`
- post-level metrics in `channel_post_metrics.py`
- comment bodies in `channel_comments.py`

Those paths currently accept several platform identifiers: `id`, `source_id`, `shortCode`, `postId`, `url`, Reddit parsed IDs, and YouTube video IDs. This is necessary because providers differ, but it must be normalized consistently so comments, media, and metrics can join without inventing duplicate post identities.

## Decision

Every official-channel post should be represented by four identity fields:

| Field | Meaning | Stability |
| --- | --- | --- |
| `platform` | Normalized platform key such as `youtube`, `instagram`, `tiktok`, `facebook`, `reddit`, `x` | Required |
| `canonical_post_uid` | Stable internal join key: `{platform}:{provider_post_id}` when available, otherwise `{platform}:url:{normalized_url_hash}` | Required for new storage |
| `provider_post_id` | Platform-native post/video ID, shortcode, parsed Reddit ID, or Facebook post ID | Preferred |
| `canonical_url` | Normalized public post URL, stripped of tracking noise where possible | Required when provider ID is missing |

Existing response fields remain backwards compatible:

- `id` stays the UI-facing primary display key.
- `source_id` stays the provider-ish ID used by current cards.
- `url` stays the public external URL.
- `external_post_id` in `vkpi_comments` keeps provider-compatible values, but new code should derive it from the canonical identity rules.
- `post_uid` in `vkpi_channel_post_metrics` is the current metrics-side version of `canonical_post_uid`.

## Platform Rules

| Platform | Preferred `provider_post_id` | URL fallback |
| --- | --- | --- |
| YouTube | video ID from `id.videoId` or `id` | `youtube.com/watch?v={id}` |
| Instagram | `shortCode`, then item `id` | post/reel permalink |
| TikTok | item `id` | `webVideoUrl` |
| Facebook | `postId`, then item `id` | post URL |
| Reddit | parsed submission ID without `t3_` | permalink containing `/comments/{id}` |
| X | tweet `id` | tweet URL |

Provider IDs must be kept platform-scoped. A raw ID is not globally unique without `platform`.

## Join Contract

The following storage and read models must join by platform-scoped post identity:

- `vkpi_channel_post_metrics.platform + post_uid`
- `vkpi_comments.platform + external_post_id`
- media cache metadata source fields when present
- frontend post actions using `post.sourceId || post.id` plus `post.url`

When a caller supplies only a URL, the service may match by `canonical_url`. When a caller supplies both ID and URL, provider ID match wins and URL is treated as a fallback.

## Non-Goals

This ADR does not migrate existing rows immediately and does not rewrite provider crawlers. It freezes the identity rules so future migrations can add explicit columns without changing semantics.

## Required Follow-Up

1. Add a shared backend helper, likely `official_post_identity.py`, that returns `platform`, `canonical_post_uid`, `provider_post_id`, and `canonical_url` from a post dict.
2. Update `channels.py`, `channel_comments.py`, and `channel_post_metrics.py` to call the shared helper instead of duplicating platform-specific ID fallback logic.
3. Add unit tests for the six platform rules above.
4. Backfill optional explicit identity fields only after the helper has parity with current production data.
