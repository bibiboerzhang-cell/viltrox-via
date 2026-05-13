# P3.13A Media UX Contract

Date: 2026-05-13
Scope: data-analysis media display contract only. This round does not claim Socialinsider-level media analytics.

## Problem

Browser QA showed several media regressions:

- account avatars sometimes fell back to initials even when platform payload had avatar fields;
- post thumbnails/video sources used different field aliases in different UI paths;
- drawer content could show broken media while the same post had a usable CDN or platform URL;
- some lists looked top-only or had original-post links missing in secondary views.

## Changes

- Added `mediaFields.ts` as one shared field-alias contract for account avatar/profile URL and post thumbnail/video/platform URL.
- Updated data-analysis cards, profile header, account drawer, drawer content tab, and home account cards to use the shared media mapping.
- Added video fallback in drawer content: proxy first, then redirect fallback if the proxied video source errors.
- Extended KOL detail data normalization to accept common Apify/Instagram/TikTok aliases such as `previewUrl`, `videoUrlNoWaterMark`, `downloadUrl`, and `downloadAddr`.
- Added a static contract smoke so future UI changes cannot silently drop avatar/media/open-original/show-all support.

## Verification

Commands run:

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile scripts/smoke_vkpi_p3_13a_media_contract.py
./scripts/run_smoke.sh smoke_vkpi_p3_13a_media_contract.py
./scripts/run_smoke.sh smoke_vkpi_p3_2_full_qa_audit.py
./scripts/run_smoke.sh smoke_vkpi_p3_1c_media_proxy.py
cd frontend && npm run build
```

Results:

- `smoke_vkpi_p3_13a_media_contract.py`: PASS
- `smoke_vkpi_p3_2_full_qa_audit.py`: PASS
- `smoke_vkpi_p3_1c_media_proxy.py`: PASS
- `npm run build`: PASS

## Remaining Work

- P3.13B: browser-driven media QA with real account pages: open all content, open original posts, verify thumbnails and playable video for the current real accounts.
- P3.13C: single-post detail and analysis flow: open post, show metrics/comments/media, run one real analysis action, and persist result.
- P4/P5: Socialinsider-level analytics such as trend charts, compare, sentiment/topic/pillar visuals, and metric picker.

## Boundary

This round hardens media field mapping and playback fallback. It does not solve every media UX issue, and it does not make the analytics page Socialinsider-complete.
