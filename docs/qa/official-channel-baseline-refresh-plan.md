# Official Channel Baseline And Refresh Plan

## Purpose

Build one complete company-owned account baseline first, then switch to cheap recent-window refreshes.

This avoids running Apify every time the page opens and keeps the employee platform usable with local database reads.

## Execution Model

1. `baseline_full_backfill`
   - Manual job only.
   - Runs once to build the company account content baseline.
   - Must have a hard item cap per platform and a confirmation gate.
   - Writes account snapshots, post snapshots, source run ids, and media cache entries.

2. `daily_recent_sync`
   - Scheduled job.
   - Runs once per day after the baseline.
   - Crawls account profile plus recent posts only.
   - Updates existing post metrics by stable `platform + post_url/post_id`.

3. `hot_post_refresh`
   - Scheduled or manual bounded job.
   - Covers only posts from the last 48 hours or latest N posts per active account.
   - Used for fast-moving views without refreshing full history.

4. `weekly_backfill`
   - Low-frequency catch-up.
   - Rechecks older posts and missing media once per week.

## Current Platform Policy

| Platform | Provider | Baseline Target | Current Safe First Batch | Daily Recent | Views Reliability | Constraint |
| --- | --- | ---: | ---: | ---: | --- | --- |
| YouTube | YouTube Data API | 1000 | 50 | 30 | High | Current crawler needs pagination for more than 50 videos. |
| Instagram | Apify Instagram posts scraper | 1000 | 100 | 30 | Medium, video/Reels only | Image posts should not be counted as playback evidence; profile `latestPosts` is only a 12-post sample. |
| TikTok | Apify TikTok | 300 | 100 | 30 | High when `playCount` is present | Do not download videos; cache covers only. |
| Facebook | Apify Facebook | 250 | 100 | 25 | Low with current actor | Page/posts actor gives engagement/media, usually not views; Reels/video path needed. |
| Reddit | PRAW or Apify Reddit | 150 | 100 | 25 | Not available | Treat as community posts plus upvotes/comments. |
| X | X API or Apify | 200 | 50 | 25 | Low/rate-limited | Supplemental until token/actor proves stable. |

## Current Dry-Run Snapshot

Generated from the local official matrix on 2026-05-17.

- Bound official accounts: 18
- Platforms: Facebook, Instagram, Reddit, TikTok, X, YouTube
- First safe batch: 975 content items
- Baseline target: 3,848 content items
- Daily recent refresh: 510 content items
- Hot refresh window: 810 content items
- Accounts needing pagination or a special actor before full baseline: 8

## Cost Controls

- Never run provider crawls when the user only refreshes the page.
- Page refresh reads local DB snapshots only.
- Full baseline requires explicit confirmation.
- Each platform has a first-batch cap before full pagination or special actors are enabled.
- `no_results` accounts must be corrected before another paid run.
- Failed accounts should cool down before retrying.
- Old posts should not be refreshed daily after baseline unless they are selected for report/audit backfill.

## Required Before Running Full Baseline

- Add or confirm pagination for YouTube history beyond 50 videos.
- Decide whether Instagram/TikTok baseline means latest 300-1000 posts or true all-history.
- Add Facebook Reels/video-specific path if playback views are required.
- Decide whether X is worth running as paid Apify or should stay low priority.
- Store post-level daily metric history instead of only overwriting `raw_payload_json`.

## Dry-Run Command

```bash
source scripts/runtime_env.sh >/dev/null
PYTHONPATH=backend .venv/bin/python scripts/vkpi_official_baseline_plan.py
```

JSON output:

```bash
source scripts/runtime_env.sh >/dev/null
PYTHONPATH=backend .venv/bin/python scripts/vkpi_official_baseline_plan.py --json
```

## Full Baseline Gate

Manual job name: `official_full_baseline`

Required confirmation: `RUN official_full_baseline`

Default per-platform limits:

- YouTube: 1000
- Instagram: 1000
- TikTok: 300
- Facebook: 250
- Reddit: 150
- X: 200

Example validate-only payload:

```json
{
  "confirm": "RUN official_full_baseline",
  "validate_only": true,
  "platforms": "youtube,instagram,tiktok,facebook,reddit,x"
}
```

Example execution payload:

```json
{
  "confirm": "RUN official_full_baseline",
  "platforms": "youtube,instagram,tiktok,facebook,reddit,x"
}
```
