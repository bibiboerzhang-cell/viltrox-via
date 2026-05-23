# ADR: Media Cache Policy

Date: 2026-05-23

## Context

V-KPI official-channel UI must show real media truth:

- cached media when V-KPI has a renderable copy
- direct/embed source when the platform supports it
- original-link fallback when media is expired, source-limited, audio-only, or intentionally not cached

The current official media audit sampled 18 official accounts and 860 posts. All sampled posts had original URLs and media candidates; 471 had cached image candidates. Some cached images are small source/provider previews, so the cache policy must separate "cached" from "high quality".

## Decision

Use three cache tiers:

| Tier | Scope | Policy |
| --- | --- | --- |
| Official accounts | Viltrox official channels and employee-bound official accounts | Cache images during official sync/prewarm within bounded limits. Keep videos as inventory/on-demand unless explicitly requested. |
| Qualified KOL subset | Future P1.X hot/warm refresh set | Lazy image cache during read/render or batch refresh. Video cache remains on-demand. |
| Legacy cold KOL records | 1021/1023 legacy KOL pool and old Excel imports | Do not daily prewarm. Keep source URLs as record/search history and refresh/cache only when search-triggered or manually qualified. |

## Storage

`VKPI_MEDIA_CACHE_STORAGE` controls storage mode:

- `local`: local file cache only.
- `hybrid`: local cache plus R2 upload when R2 env is configured.
- `r2`: R2-backed public URL when upload succeeds.

R2 failure must not hide a valid local cache result. The cache status should remain visible as local-only or upload-failed rather than becoming a blank card.

## Images

Images are safe to cache automatically when all are true:

- source URL host is allowlisted by `media_cache.py`
- content type and byte limits pass cache validation
- per-sync limits such as `VKPI_MEDIA_CACHE_MAX_IMAGES` and timeout caps are respected
- caching is attached to official sync/prewarm, search-triggered refresh, or an explicit operator task

Image quality must be described as a separate property from cache presence. A cached 472x590 image is cached but not necessarily high-resolution.

## Videos

Video cache is not automatic for every discovered video.

Default behavior:

- YouTube renders through embed URL when video ID is available.
- Instagram and TikTok item video cache remains explicit/on-demand.
- Video prewarm scripts default to inventory/manifest mode, not download mode.
- Download mode requires explicit operator choice and byte/time caps.

UI must show "open original" or source status when V-KPI does not hold a playable cache. It must not pretend a poster image is a playable video.

## Failure Contract

Media status must remain explainable:

- `cached`: V-KPI has a renderable cache URL.
- `source_only`: original URL exists but no cache exists.
- `source_limited`: provider URL is expired, blocked, unsupported, or too small/low quality.
- `embed`: playable through platform embed, usually YouTube.
- `inventory_only`: video URL is known but not downloaded.
- `failed`: cache attempt failed with a recorded reason.

These statuses may be implemented incrementally; the immediate rule is that UI copy and API payloads must not collapse them into a fake image/video success.

## Operational Limits

- Official image prewarm should stay bounded by `VKPI_MEDIA_CACHE_MAX_IMAGES` and `VKPI_MEDIA_CACHE_TIMEOUT`.
- Video downloads must use max-byte caps and remain manual or task-based.
- R2 migration or cleanup must stay backup-first and should not delete local cache until R2 has been stable for a measured window.
- P1.X Apify batching must not be used to prewarm all legacy cold KOL media.

## Required Follow-Up

1. Add explicit media status fields to official content API responses.
2. Make card UI display `source_only`, `inventory_only`, and `failed` distinctly.
3. Keep `scripts/vkpi_prewarm_official_media.py` defaulting video mode to manifest/inventory.
4. Add a small media-status audit that counts cached, source-only, inventory-only, and failed media by platform.
