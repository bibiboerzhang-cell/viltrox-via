# Viltrox 2.0

Parallel rebuild of the Viltrox platform.

## Goals

- Keep the legacy `/Users/jianbozhang/Downloads/viltrox-app-test/viltrox-test` runtime untouched.
- Rebuild public index and admin as a new 2.0 surface with cleaner frontend and backend boundaries.
- Preserve creator auth, creator code, social binding, student QR identity, uploads, queueing, and Via intelligence as migration inputs.
- Keep the shareable project small enough to hand off without bundling virtualenvs, uploads, or toolchains.

## Current shape

- `backend/app`: copied service and router baseline, now safe to evolve separately.
- `frontend/src`: new route-first app shell with `/`, `/admin`, `/account`, `/redeem`.
- `migrations/*.sql`: Postgres baseline + 2.0 runtime migrations, including the new job ledger.
- `scripts/package_share.sh`: builds a lightweight share archive.
- `scripts/make_debug_zip.sh`: builds a safer debug archive with env files, databases, secrets, caches, and build artifacts excluded by default.
- `scripts/start_public.sh`, `scripts/start_admin.sh`, `scripts/start_worker.sh`: production-style launch helpers for `public-web`, `admin-web`, and `worker`.

## Running 2.0 locally

1. Copy `.env.example` to `.env`.
2. Bring up Postgres and Redis. 2.0 production mode requires both.
3. Build the frontend from `frontend/` with `npm install && npm run build`.
4. Start the runtime roles:

```bash
./scripts/start_public.sh
./scripts/start_admin.sh
./scripts/start_worker.sh
```

Default local ports:

- public web: `8101`
- admin web: `8102`
- worker: background process only

## Production runtime

- `public-web`: Gunicorn + Uvicorn workers (default `4`)
- `admin-web`: Gunicorn + Uvicorn workers (default `2`)
- `worker`: dedicated background worker process, default `15` async consumers per process
- `database`: pooled Postgres runtime
- `queue`: Redis Streams + Postgres job ledger
- `realtime`: event-driven SSE via Redis-backed task events
- `cache`: Redis-backed hot-path cache
- `limits`: Redis-backed tiered rate limiting

## Immediate next build targets

1. Finish reconnecting auth, binding, student, upload, and Via business flows onto the new substrate.
2. Tighten triple-model audit execution and provider degradation behavior.
3. Expand cache coverage on creator, student, and intelligence hot reads.
