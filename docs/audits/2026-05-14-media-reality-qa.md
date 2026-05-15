# P4 Step 19 Media Reality QA

Date: 2026-05-14
Backup: `/Users/bibiboer/Documents/V-KPI-backups/before-p4-step19-media-reality-qa-20260514-153906.tar.gz`
Scope: data-analysis media reality: account avatar, post image/video, original post links, post detail drawer, single-post analysis.

## Result

Status: **completed with degraded media freshness**

The media UX is not fake: image proxy, video proxy, original post links, post detail drawer, and single-post analysis all have real paths. The remaining issue is data freshness for Instagram CDN video URLs: some stored MP4 URLs return 403 and need refreshed crawl or archival download to become reliably playable.

## Live Asset Audit

Command:

```bash
PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_media_live_assets.py
```

Output summary:

```text
accounts_avatar_present=2
accounts_profile_url_present=3
accounts_total=3
posts_platform_url_present=10
posts_thumbnail_present=10
posts_total=10
posts_video_present=4
image_samples=4 ok=4
video_samples=3 ok=1
video_sample_1=fail HTTPError: 403
video_sample_2=fail HTTPError: 403
video_sample_3=ok 206 video/mp4
missing_avatar_accounts=instagram:godox#742[not_configured]
AUDIT_STATUS=degraded:some_media_missing_or_expired
```

Interpretation:

- Images are currently healthy for sampled assets.
- Video proxy path works for at least one live MP4.
- Two Instagram video MP4 URLs are expired/blocked and return 403.
- One account is missing avatar because the account is still `not_configured`.

## Browser QA

Target: `http://127.0.0.1:5173/#dataAnalysis`
Account tested: `Godox Global` / Instagram

Observed:

- Account profile avatar loaded through `/api/admin/vkpi/media/image-proxy`.
- Content tab shows `显示 5 / 5 条内容`.
- Page contains real media elements: `img=6`, `video=1` on Content tab; `img=7`, `video=2` after opening post detail.
- `打开平台主页` points to `https://www.instagram.com/godox_global`.
- Post links point to real Instagram post URLs, e.g. `https://www.instagram.com/p/DYLlec3zX6e/`.
- A failed video shows explicit fallback text: `视频链接失效，打开原帖`.
- Post detail drawer opens from `单帖详情 / 分析` and shows caption, views, likes, comments, engagement.
- `运行单帖分析` made a real backend call and returned:
  - `Status: done`
  - `Score: 70`
  - `Method: gemini_fileapi_Instagram_gemini-2.5-flash`
  - `Providers: openai + gemini + claude`
  - Chinese summary rendered in the drawer.

## Matched Smoke Tests

All matched media contract smokes passed before browser QA:

```text
smoke_vkpi_p4_4_media_ux_contract.py PASS
smoke_vkpi_p4_4d_content_actions.py PASS
smoke_vkpi_p4_4c_post_analysis_display.py PASS
smoke_vkpi_p3_13c_post_detail_contract.py PASS
```

## Remaining Issues

1. Instagram CDN MP4 URLs can expire or return 403; the UI fallback works, but dependable playback requires refreshed crawl or local archival copy.
2. Single-post analysis is synchronous and can take 90-120 seconds for video URLs; it works, but UX should eventually move to async job status or clearer elapsed-time feedback.
3. `instagram:godox#742` missing avatar is a data/configuration issue, not a rendering issue.
4. Top bar still shows `BE checking` despite backend traffic being healthy; this is a separate version/health indicator issue, not a media blocker.

## Acceptance

P4 Step 19 is accepted for media reality QA because:

- Real media proxy paths exist.
- Real original-post links exist.
- Drawer detail is real.
- Single-post analysis returns real multi-provider output.
- Known degradation is documented and scoped to expired/blocked CDN media freshness.
