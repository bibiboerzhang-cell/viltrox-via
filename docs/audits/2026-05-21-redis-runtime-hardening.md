# Redis Runtime Hardening Audit

Date: 2026-05-21

## Scope

Enable local Redis on the `viltroxtest.com` deployment for shared cache and shared rate-limit state.

This package does not enable scheduler jobs, async workers, provider calls, Apify sync, or KOL deep scan.

## Remote Backup

Before changing `.env`, a backup was created:

- `/opt/viltrox-2.0/runtime/env-backups/env-pre-redis-20260520T205714Z.env`

## Remote Changes

Installed Ubuntu packages:

- `redis-server`
- `redis-tools`
- `libjemalloc2`
- `liblzf1`

Redis service:

- `redis-server.service` enabled
- `redis-server.service` active
- Redis version: `7.0.15`
- Bound locally only:
  - `127.0.0.1:6379`
  - `[::1]:6379`

Environment updates:

- `REDIS_URL=redis://127.0.0.1:6379/0`
- `REDIS_NAMESPACE=viltrox-2.0-test:runtime`

Preserved disabled runtime controls:

- `APP_ROLE=web`
- `ENABLE_SCHEDULER=0`
- `VKPI_ASYNC_ENABLED=0`

## Verification

Health:

- `/health` returned `200`
- `git_short_sha=d9d6c125`
- `client_matches_server=true`

Redis:

- `redis-cli ping` returned `PONG`
- `systemctl is-active redis-server` returned `active`

Rate limit:

- `/api/admin/intel/system/rate-limit` reports `backend=redis`
- Fake local login smoke:
  - requests 1-10 returned normal invalid-login response
  - request 11 returned `429`
  - `X-RateLimit-Bucket=login_register`
- Redis key observed:
  - `viltrox-2.0-test:runtime:ratelimit:login_register:ip:127.0.0.1`

Cache:

- `/api/admin/intel/system/cache` reports `backend=redis`
- `official-matrix` first request: `455.8ms`
- `official-matrix` second request: `14.3ms`
- `kol-pool summary` first request: `17.4ms`
- `kol-pool summary` second request: `4.2ms`
- Redis cache keys observed:
  - `viltrox-2.0-test:runtime:cache:vkpi:channels:official_matrix:all:limit:20`
  - `viltrox-2.0-test:runtime:cache:vkpi:kol_pool:summary:`

## Remaining Notes

- Redis is currently a single local instance on the Hetzner node, appropriate for this test deployment.
- If worker processes are split later, reuse this namespace instead of inventing a second Redis namespace.
- Do not enable Redis-backed job queues until the scheduler/worker package explicitly starts.
