# ADR: Official Account Full-Scope Refresh Strategy

Decision date: 2026-05-23

## Context

The daily official-channel timer now runs a bounded recent refresh, currently `scripts/cron_daily_sync.py --official-max-posts 50 --skip-kol`. That keeps the 18 company-owned accounts fresh without touching the legacy KOL pool.

Recent-window provider samples must not be interpreted as a complete account history. A provider returning latest 30 or latest 50 posts can make account-level cumulative totals appear smaller than the previous baseline. The UI now exposes this as `baseline_protected`; post-level deltas provide the honest activity signal.

## Decision

Separate official-account refresh into two modes:

1. `daily_recent_sync`
   - Scheduled.
   - Bounded to recent posts only.
   - Safe to run daily after sync guard passes.
   - Must not reset or shrink account-level cumulative baseline.
   - Must write post-level deltas when post rows are present.

2. `official_full_baseline`
   - Manual only.
   - Requires backup-first execution and explicit operator confirmation.
   - Uses platform-specific full-scope caps.
   - Used to establish or repair the historical baseline, not for normal daily freshness.

## Current Daily Policy

The production timer is official-only and intentionally excludes legacy KOL pool refresh:

```bash
.venv/bin/python scripts/cron_daily_sync.py --official-max-posts 50 --skip-kol
```

The `50` value is a recent-window cap, not a declaration that each account has only 50 posts. If this run returns fewer rows or lower cumulative totals, `cumulative_floor` keeps the previous baseline and the UI must display `baseline_protected`.

## Full-Scope Policy

Use the existing full-baseline gate instead of changing the daily timer:

```bash
JOB_NAME=official_full_baseline PAYLOAD_JSON='{"confirm":"RUN official_full_baseline"}' scripts/ops/run_prod_vkpi_job.sh
```

Default full-scope caps remain:

| Platform | Full-scope cap | Daily recent cap | Notes |
|---|---:|---:|---|
| YouTube | 1000 | 50 | API quota first, Apify fallback only on quota/provider failure. |
| Instagram | 500 | 50 | Video/Reels views are evidence; image posts are not playback evidence. |
| TikTok | 300 | 50 | Keep video downloads disabled. |
| Facebook | 250 | 50 | Page/posts actor is engagement/media evidence; playback views remain low confidence without Reels path. |
| Reddit | 150 | 50 | Treat as posts plus score/comments, not views. |
| X | 200 | 50 | Supplemental until provider stability is confirmed. |

## Data Semantics

- Account cumulative fields are protected by `cumulative_floor`.
- Per-post metrics are stored in `vkpi_channel_post_metrics`.
- Daily user-facing growth should prefer post-level positive deltas when account cumulative totals are protected.
- First-seen older posts must not create fake growth.
- Missing views are a data-availability state, not zero playback.

## Operational Guardrails

- Do not run full baseline from page load or UI refresh.
- Do not run full baseline automatically from `vkpi-sync-daily.timer`.
- Do not use `official_full_baseline` to touch the 1021/1023 legacy KOL pool.
- Run a production backup before any manual full baseline.
- Run `scripts/vkpi_official_baseline_plan.py --json` before full baseline to confirm account count, platform mix, caps, and accounts needing special handling.

## Acceptance

- Daily timer remains official-only recent refresh.
- Latest 30/50 samples cannot lower displayed historical totals without `baseline_protected`.
- Full-scope refresh has a separate manual gate and cap table.
- Official matrix can explain whether a value is real growth, protected baseline, or unavailable source data.
