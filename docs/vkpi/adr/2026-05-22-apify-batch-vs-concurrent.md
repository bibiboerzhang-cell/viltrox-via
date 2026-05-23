# ADR: Apify Batch Input Before Multi-Run Concurrency

Decision date: 2026-05-22

## Context

V-KPI needs to refresh qualified KOL profile data without returning to the old daily full-pool pattern. The question was whether to use one Apify run with multiple URLs, multiple single-URL runs in parallel, or sequential single-URL runs.

Two small live spikes were run against company-owned or known Viltrox accounts only. They were profile-only checks and did not write to the database.

Evidence files:

- `/opt/viltrox-2.0/runtime/ops/20260523T001348Z-apify-bulk-spike/summary.json`
- `/opt/viltrox-2.0/runtime/ops/20260523T001544Z-apify-concurrent-spike/summary.json`

## Empirical Results

| Platform | Strategy | Runs | URLs | Wall time | Items |
|---|---:|---:|---:|---:|---:|
| Instagram | one batch run | 1 | 3 | 7.576s | 3 |
| Instagram | single-URL runs, concurrent | 3 | 3 | 13.527s | 3 |
| Instagram | single-URL runs, sequential | 3 | 3 | 19.040s | 3 |
| TikTok | one batch run | 1 | 3 | 34.294s | 3 |
| TikTok | single-URL runs, concurrent | 3 | 3 | 39.893s | 3 |
| TikTok | single-URL runs, sequential | 3 | 3 | 86.428s | 3 |

A separate 5-account batch spike also succeeded for Instagram and TikTok. Platform-to-KOL mapping is feasible from Instagram `username/inputUrl/url` and TikTok `authorMeta.name/profileUrl`.

## Decision

Use Apify batch input first, with bounded multi-run concurrency only as an outer scheduler:

- Group KOL refresh targets by platform.
- Split each platform into platform-specific chunks.
- Start one Apify run per chunk.
- Limit global concurrent Apify runs with a semaphore.
- Do not default to one run per KOL.

Initial chunk defaults:

| Platform | Chunk size |
|---|---:|
| instagram | 50 |
| youtube | 50 |
| facebook | 50 |
| reddit | 50 |
| x | 50 |
| tiktok | 25 |

Initial concurrency defaults:

- `max_concurrent_runs = 2`
- Raise to `3` only after stable runtime, success-rate, and cost observations.
- Keep the value at or below half of the account's Apify concurrent-run limit.

## Rationale

Apify actor internal batching beat external per-KOL concurrency in the measured cases. Batch input reduces run startup overhead, dataset polling overhead, and concurrent-run slot pressure. TikTok remains slower than Instagram, so its chunk size stays lower.

This optimization must not be used to speed up the old 1021/1023 KOL full refresh. It is only valid after the P1.X.A selector limits refresh scope to a qualified subset.

## Implementation Boundary

P1.X.B may implement:

- `run_apify_batch(platform, urls)`
- platform chunking
- bounded multi-run scheduling
- dataset-to-`kol_pool_id` mapping
- chunk-level failure tracking and retry queueing

P1.X.B must not implement:

- per-KOL single runs as the default path
- unlimited concurrent runs
- webhook orchestration
- daily full legacy KOL refresh

Re-measure quarterly because Apify actor performance and plan limits can drift.
