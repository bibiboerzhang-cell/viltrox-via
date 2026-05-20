# Performance P1 Audit

Date: 2026-05-21

## Scope

Performance P1 for the live `viltroxtest.com` deployment.

This package focuses on diagnostics, Redis cache/rate-limit enablement, and read-path caching. It does not run providers, Apify sync, 1012 KOL deep scan, scheduler jobs, or business data backfills.

## Current Live Build

- Commit: `da70e77 fix(vkpi): cache competitor dashboard summary`
- Service: `viltrox-2.0-test.service`
- Runtime: `/opt/viltrox-2.0`

## Redis Runtime

Redis is installed and active on the Hetzner node:

- `redis-server=active`
- `redis-cli ping=PONG`
- local-only listener: `127.0.0.1:6379`
- app env:
  - `REDIS_URL=redis://127.0.0.1:6379/0`
  - `REDIS_NAMESPACE=viltrox-2.0-test:runtime`

Preserved controls:

- `APP_ROLE=web`
- `ENABLE_SCHEDULER=0`
- `VKPI_ASYNC_ENABLED=0`

## Database Index Check

No new DB migration was required in this pass.

Existing live indexes cover the hot tables:

- `vkpi_channel_metrics`
  - `idx_vkpi_channel_metrics_channel_date`
  - `idx_vkpi_channel_metrics_date`
  - `idx_vkpi_channel_metrics_latest`
  - unique `(channel_id, snapshot_date)`
- `vkpi_employee_channels`
  - staff/status, platform/status, sync, active platform/handle indexes
- `vkpi_kol_pool`
  - platform/handle
  - platform/score
  - score
  - source/created
- `vkpi_brand_signal`
  - new/detected
  - kol/detected
  - type/role/detected
- `vkpi_competitor_relation`
  - brand/risk
  - kol/risk
  - risk tier/score
  - unique `(kol_pool_id, competitor_brand)`

Live row counts at inspection:

- `vkpi_channel_metrics=55`
- `vkpi_employee_channels=20`
- `vkpi_kol_pool=1023`
- `vkpi_brand_signal=2479`
- `vkpi_competitor_relation=6072`

## Code Change

Added read-through cache for persisted competitor dashboard summaries:

- file: `backend/app/services/vkpi/kol_competitor_detector.py`
- cache prefix: `vkpi:competitors:dashboard:`
- TTL: `300s`
- invalidation: `persist_competitor_relations()` clears this prefix after writes.

This keeps rule results unchanged and only avoids repeated parsing of the 6072 persisted competitor relation rows.

## Benchmarks

Measured from the server against `127.0.0.1:8001` with admin auth.

After Redis/runtime cache:

| Endpoint | Round 1 | Round 2 | Round 3 |
|---|---:|---:|---:|
| dashboard | 62.7ms | 41.7ms | 40.4ms |
| official matrix | 201.4ms | 14.9ms | 13.6ms |
| KOL pool summary | 14.0ms | 4.8ms | 3.9ms |
| KOL pool list | 18.1ms | 6.5ms | 9.7ms |
| competitors dashboard | 272.5ms | 7.1ms | 5.2ms |
| brand signals | 16.6ms | 14.9ms | 17.0ms |

Cache hit fields observed:

- official matrix: `false -> true -> true`
- KOL pool summary: `false -> true -> true`
- KOL pool list: `false -> true -> true`
- competitors dashboard: `false -> true -> true`

## Verification

Tests:

- `py_compile backend/app/services/vkpi/kol_competitor_detector.py`
- `pytest tests/test_vkpi_product_analysis_competitor.py tests/test_vkpi_kol_pool.py`
- result: `8 passed`

Live:

- `/health` returned `200`
- `client_matches_server=true`
- Redis: `PONG`
- service: `active/running`
- CSP/HSTS from previous hardening remain active:
  - `connect-src 'self'`
  - `strict-transport-security: max-age=31536000; includeSubDomains`

## Remaining Work

- Frontend skeleton states are still separate from this P1 backend/runtime pass.
- No global scheduler was enabled.
- No provider or KOL deep scan was run.
