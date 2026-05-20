# V-KPI Performance Diagnostics - 2026-05-20

## Scope

This pass measured high-traffic V-KPI read paths before UI-1 work. It does not change business data or provider behavior.

## Environment

- Host: `viltrox`
- App: `/opt/viltrox-2.0`
- DB: `viltrox2_test`
- Scheduler: `ENABLE_SCHEDULER=0`
- Redis: `REDIS_URL` not configured in `.env`

## Findings

| Check | Avg before | Payload | Finding |
|---|---:|---:|---|
| `channels.list_channels` | 9.75 ms | 26 KB | Fine |
| `channels.official_account_matrix` | 2880.81 ms | 1465 KB | Slow, CPU/file parsing heavy |
| `channels.official_views_evidence` | 2919.04 ms | 1583 KB | Slow because it rebuilds matrix |
| `channels.team_overview` | 0.95 ms | <1 KB | Fine |
| `kol_pool.list_pool.fit` | 10.47 ms | 502 KB | Query is fine, payload is large |
| `kol_pool.list_pool.query` | 0.57 ms | 2 KB | Fine |
| `kol_history.search.viltrox` | 12.30 ms | 9 KB | Fine |

## Changes

- Added 5-minute cache to:
  - `channels.official_account_matrix`
  - `channels.official_views_evidence`
- Cache uses existing `app.services.cache`:
  - Redis if `REDIS_URL` is configured later.
  - In-process memory fallback now.
- Cache is cleared after channel bind, unbind, and sync.
- Added targeted performance indexes in `migrations/065_vkpi_perf_indexes.sql`.
- Added `scripts/ops/benchmark_vkpi_perf.sh`.

## Local Verification

`official_account_matrix`:
- First call: ~3168 ms
- Second call: ~0.01 ms

`official_views_evidence`:
- First call after matrix cache: ~0.75 ms
- Second call: ~0.01 ms

## Next Diagnostics

- Browser Network should confirm whether remaining delay is payload transfer/render rather than backend compute.
- Bundle split is still open: built app chunk is ~212 KB, but one lazy chunk is ~594 KB.
- R2/CDN remains needed for media-heavy views.
