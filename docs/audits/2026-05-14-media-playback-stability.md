# P4 Step 20 - Media Playback Stability

Date: 2026-05-14
Scope: data-analysis media playback only
Status: completed with known external-CDN degradation

## Goal

Step 19 proved that media, original-post links, post drawer, and single-post analysis are real. It also found that Instagram CDN video URLs can expire and return 403. Step 20 narrows the fix to playback fallback stability without building a heavy archival system.

## Code Changes

Frontend-only playback fallback upgrade:

- `frontend/src/components/vkpi/pages/data-analysis/utils/mediaFields.ts`
  - Added `postVideoUrls()` to collect multiple video URL candidates from normalized fields and raw payload JSON.
- `frontend/src/components/vkpi/pages/data-analysis/utils/mediaProxy.ts`
  - Added `playbackVideoCandidates()` to expand raw video URLs into same-origin proxy and authenticated redirect candidates.
- `frontend/src/components/vkpi/pages/data-analysis/shared/PostCard.tsx`
  - Tries playback candidates sequentially before showing the original-post fallback.
- `frontend/src/components/vkpi/pages/data-analysis/drawers/PostDetailDrawer.tsx`
  - Uses the same sequential candidate playback strategy and resets candidate state when switching posts.
- `frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx`
  - Content grid uses sequential playback candidates instead of a single proxy/redirect pair.
- `scripts/smoke_vkpi_p4_4_media_ux_contract.py`
  - Updated the static contract to require multi-candidate playback.
- `scripts/smoke_vkpi_p3_13c_post_detail_contract.py`
  - Updated the single-post drawer contract to require multi-candidate playback.

## Live Browser QA

Target: `http://127.0.0.1:5173/#dataAnalysis`

Observed page state:

- Data Analysis page loaded.
- Account detail visible for `Godox Global`.
- Content tab visible with `显示 5 / 5 条内容`.
- Post cards render real media and real original-post links.
- When a video URL is expired, UI shows `视频链接失效，打开原帖` instead of a dead blank video.
- Single-post drawer remains wired to detail and analysis actions.

Important: the visible Godox failed video still uses an expired Instagram CDN URL, so it cannot be recovered unless another valid candidate exists in raw payload or the account is refreshed. The new code prevents first-candidate failure from blocking playback when alternate candidates exist.

## Live Asset Audit

```text
VKPI_MEDIA_LIVE_ASSET_AUDIT
accounts_avatar_present=2
accounts_profile_url_present=3
accounts_total=3
posts_platform_url_present=10
posts_thumbnail_present=10
posts_total=10
posts_video_present=4
image_samples=4 ok=4
image_sample_1=ok image/jpeg
image_sample_2=ok image/jpeg
image_sample_3=ok image/jpeg
image_sample_4=ok image/jpeg
video_samples=3 ok=1
video_sample_1=fail HTTPError: 403
video_sample_2=fail HTTPError: 403
video_sample_3=ok 206 video/mp4
missing_avatar_accounts=instagram:godox#742[not_configured]
AUDIT_STATUS=degraded:some_media_missing_or_expired
```

## Validation

```text
npm run build
PASS

./scripts/run_smoke.sh smoke_vkpi_p4_4_media_ux_contract.py
PASS

./scripts/run_smoke.sh smoke_vkpi_p3_1c_media_proxy.py
PASS

./scripts/run_smoke.sh smoke_vkpi_p4_4d_content_actions.py
PASS

./scripts/run_smoke.sh smoke_vkpi_p3_13c_post_detail_contract.py
PASS

python vkpi-p4-agent-package-v1.1/scripts/verify_agent_boundary.py ...
BOUNDARY_OK

PYTHONPATH=backend .venv/bin/pytest tests/ -q
85 passed, 106 warnings, 5 subtests passed
```

## Remaining Issues

1. Expired Instagram CDN URLs still return 403 if no alternate video URL exists in the payload.
2. To make playback fully durable, a future round should add one of:
   - refresh account/post crawl before playback,
   - background archive of successful video URLs,
   - local/R2 media copy during crawl,
   - or async refresh action on failed media cards.
3. `instagram:godox#742` is missing avatar because it is still `not_configured`; that is a data/configuration issue, not a playback component issue.
4. Top-bar `BE checking` remains separate from media playback.

## Decision

Step 20 should be considered a frontend playback fallback stability improvement, not a full media archival solution. It is safe for P4 because it improves current UX without expanding crawler cost or storage behavior.
