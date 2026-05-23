# Official Media Source Audit

Generated: 2026-05-23 UTC

Scope: read-only audit of the 18 official accounts in the channel matrix. This did not call Apify, YouTube, Gemini, LLM, or any crawler.

Evidence artifacts:

- `runtime/qa/20260523-p1-baseline/official-media-source-audit.json`
- `runtime/qa/20260523-p1-baseline/official-media-image-dimensions.json`
- `runtime/qa/20260523-p1-baseline/channels-baseline-protected.png`

## Summary

The current official content matrix is not missing media at the API contract level.

- Accounts: `18`
- Posts sampled by official matrix: `860`
- Posts with original URL: `860/860`
- Posts with any media candidate: `860/860`
- Posts with image candidate: `860/860`
- Posts already using `/api/vkpi-media/image-cache`: `471/860`
- Posts with video URL: `139/860`
- YouTube embed candidates: `50/50`

## Platform Coverage

| Platform | Posts | Original URL | Any media | Cached image | Video URL | Notes |
|---|---:|---:|---:|---:|---:|---|
| Facebook | 200 | 200 | 200 | 95 | 0 | Current actor provides image/media and engagement; playback views remain low confidence without Reels path. |
| Instagram | 300 | 300 | 300 | 31 | 114 | External IG image URLs remain in `image_urls`; frontend falls back to cached `media_url` when possible. |
| Reddit | 10 | 10 | 10 | 9 | 2 | Views unavailable by platform contract; score/comments are usable. |
| TikTok | 250 | 250 | 250 | 236 | 0 | Cover image cache is mostly healthy; video cache remains explicit/on-demand. |
| X | 50 | 50 | 50 | 50 | 23 | Supplemental source; media is present. |
| YouTube | 50 | 50 | 50 | 50 | 0 | Embeds are derived from video IDs rather than `video_url`. |

## Image Resolution

30 cached image samples were inspected from local cache with Pillow:

- `30/30` cached files existed.
- `12/30` were narrower than `480x270` in at least one dimension.
- Many small samples are vertical Facebook images around `472x590`, `512x640`, or `540x960`.
- The largest sampled asset was `1459x540`.

The content card CSS uses:

```css
aspect-ratio: 16 / 9;
object-fit: contain;
```

So the main content cards are not cropping or stretching media with `cover`. Apparent blur on these cards is more likely source/cache resolution or provider-selected preview size, not CSS cover-cropping.

## Current Interpretation

No immediate crawler run is needed for media source triage.

The next repair should be field-quality focused:

- Prefer highest-resolution provider fields before generic `thumbnailUrl`.
- Keep YouTube `maxres -> standard -> high -> medium -> default`.
- For Instagram, prefer `displayResources` largest candidate and child post `displayUrl` before low-resolution thumbnails.
- For TikTok, continue preferring `videoMeta.originalCoverUrl` / `coverUrl`.
- For Facebook, inspect whether the actor exposes larger image variants than the current `media/image/picture` path.
- Keep `打开原帖` visible for media that is source-limited, expired, audio-only, or intentionally not cached.

## Acceptance

This satisfies the "source blurry vs UI enlargement" split:

- Source/cache layer has concrete sampled dimensions.
- UI layer is confirmed to use contained media rendering.
- API layer exposes original links and media candidates for all sampled official posts.
- YouTube can render through embeds without direct video files.
