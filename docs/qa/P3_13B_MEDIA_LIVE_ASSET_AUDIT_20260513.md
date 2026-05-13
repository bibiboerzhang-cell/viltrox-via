# P3.13B Media Live Asset Audit

Date: 2026-05-13
Scope: live-data media asset audit and expired-video UX fallback. This round does not call Apify, YouTube, or LLM providers.

## Why

The UI previously showed broken media blocks when stored platform CDN video URLs were expired or rejected. P3.13A unified field mapping, but the real data still needed a live-asset check.

## Live Audit Result

Command:

```bash
PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_media_live_assets.py
```

Observed result:

```text
accounts_total=3
accounts_avatar_present=2
accounts_profile_url_present=3
posts_total=10
posts_thumbnail_present=10
posts_video_present=4
posts_platform_url_present=10
image_samples=4 ok=4
video_samples=3 ok=1
video_sample_1=fail HTTPError: 403
video_sample_2=fail HTTPError: 403
video_sample_3=ok 206 video/mp4
missing_avatar_accounts=instagram:godox#742[not_configured]
AUDIT_STATUS=degraded:some_media_missing_or_expired
```

## Interpretation

- Image/thumb path is currently healthy for sampled rows.
- Current DB has 10 posts, all have thumbnails and platform URLs.
- Stored video URLs are partially expired or blocked: 2 of 3 sampled video range checks returned 403.
- One account still lacks avatar because its account crawl status is `not_configured` / profile lookup was not a successful real profile result.

## Fix Applied

- Post cards and drawer content now try backend video proxy first.
- If proxy fails, they switch to authenticated redirect fallback.
- If redirect also fails, they stop rendering a broken video element, fall back to thumbnail/placeholder, and show `视频链接失效，打开原帖`.
- Added `audit_vkpi_media_live_assets.py` for repeatable live-data media health checks.

## Verification

Commands run:

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile scripts/smoke_vkpi_p3_13a_media_contract.py scripts/audit_vkpi_media_live_assets.py
./scripts/run_smoke.sh smoke_vkpi_p3_13a_media_contract.py
./scripts/run_smoke.sh smoke_vkpi_p3_2_full_qa_audit.py
./scripts/run_smoke.sh smoke_vkpi_p3_1c_media_proxy.py
cd frontend && npm run build
```

Results:

- P3.13A media contract smoke: PASS
- P3.2 full QA audit smoke: PASS
- P3.1C media proxy smoke: PASS
- Frontend build: PASS

## Remaining Work

- Browser-controlled page QA is still blocked by the local browser automation layer returning `ERR_BLOCKED_BY_CLIENT` on `127.0.0.1:5173`; manual browser validation is still required.
- P3.13C should add single-post detail / single-post analysis, not just media display.
- Long-term fix for expired videos is a refresh/download strategy: refresh CDN URLs before analysis, store durable derived thumbnails, and only store persistent video files when analysis really requires it.
