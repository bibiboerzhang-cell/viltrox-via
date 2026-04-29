# Viltrox 2.0 Backend Deployment

This document is the production-oriented deployment guide for `viltrox-2.0`.

## Runtime Model

`viltrox-2.0` runs as a 3-role stack:

- `public-web`
- `admin-web`
- `worker`

The 2.0 backend is production-first:

- `Postgres` is mandatory
- `Redis` is mandatory
- `DB_RUNTIME_BACKEND=postgres` is mandatory
- `SQLite` and in-process-only runtime fallbacks are not valid production modes

## Required Services

- Postgres primary
- Redis
- Gunicorn for `public-web` and `admin-web`
- Dedicated Python worker process for `worker`

## Required Environment

Minimum required variables:

- `ENVIRONMENT=production`
- `V2_PRODUCTION_MODE=1`
- `APP_ROLE=public-web|admin-web|worker`
- `DB_RUNTIME_BACKEND=postgres`
- `DATABASE_URL=postgresql://...`
- `DATABASE_POOL_URL=postgresql://...` (recommended with PgBouncer)
- `DB_USE_PGBOUNCER=1` (recommended in staging/production)
- `REDIS_URL=redis://...`
- `JWT_SECRET=...`
- `JWT_SECRET_PREVIOUS=...` (optional comma-separated rotation window)
- `ADMIN_PASSWORD=...`

Recommended:

- `APP_STACK_NAME=viltrox-2.0-production`
- `ENV_FILE=/etc/viltrox/viltrox-2.0.env.production`
- `REDIS_NAMESPACE=v2`
- `PUBLIC_GUNICORN_WORKERS=4`
- `ADMIN_GUNICORN_WORKERS=2`
- `VIDEO_WORKERS=30`
- `SITE_URL=https://lab.viltrox.com`
- `ADMIN_SITE_URL=https://admin.viltrox.com`

## Environment Isolation

`viltrox-2.0` supports layered env loading to reduce dev/staging/prod mixups:

1. `.env` (base)
2. `.env.<ENVIRONMENT>` (overlay)
3. `ENV_FILE` (final explicit override)

For production/staging, always set:

- `ENVIRONMENT=production` or `staging`
- `APP_STACK_NAME` with environment suffix
- `ENV_FILE` to role-specific env file

Sample files are provided in:

- `deploy/env/.env.local.example`
- `deploy/env/.env.staging.example`
- `deploy/env/.env.production.example`

## PgBouncer Setup (Optional but Recommended)

Templates:

- `deploy/pgbouncer/pgbouncer.ini.example`
- `deploy/systemd/pgbouncer.service.example`

When PgBouncer is enabled:

- App points to `DATABASE_POOL_URL` (port `6432`)
- App keeps `DATABASE_URL` for direct connectivity checks and migrations
- Runtime disables prepared statements (`prepare_threshold=None`) for transaction pooling compatibility

## Migrations (Alembic + SQL Bridge)

`viltrox-2.0` now exposes an Alembic entrypoint while keeping the existing SQL files in `/migrations`.

- Alembic config: `alembic.ini`
- Bridge revision: `alembic/versions/20260428_0001_bridge_existing_sql_migrations.py`

Run migrations in Postgres environments:

```bash
cd /Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0
bash ./scripts/alembic_upgrade.sh
```

Notes:
- Bridge revision is Postgres-only.
- Downgrade is intentionally blocked; use snapshot restore for rollback.

## Local Stack

Start local Postgres + Redis:

```bash
cd /Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0
./scripts/start_local_stack.sh
./scripts/check_local_stack.sh
```

## Role Startup

Public web:

```bash
cd /Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0
source ./scripts/runtime_env.sh
APP_ROLE=public-web ./scripts/start_public.sh
```

Admin web:

```bash
cd /Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0
source ./scripts/runtime_env.sh
APP_ROLE=admin-web ./scripts/start_admin.sh
```

Worker:

```bash
cd /Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0
source ./scripts/runtime_env.sh
APP_ROLE=worker ./scripts/start_worker.sh
```

Worker cluster profiles:

```bash
cd /Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0
source ./scripts/runtime_env.sh
./scripts/start_worker_cluster.sh
```

Available worker tiers:

- `WORKER_CLUSTER_TIER=60`: `4` worker processes x `15` async consumers.
- `WORKER_CLUSTER_TIER=120`: `8` worker processes x `15` async consumers.
- `WORKER_CLUSTER_TIER=300`: `20` worker processes x `15` async consumers.
- `WORKER_CLUSTER_TIER=custom`: use explicit `WORKER_SERVICE_PROCESSES` and `WORKER_ASYNC_CONSUMERS`.

300-worker staging shortcut:

```bash
cd /Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0
source ./scripts/runtime_env.sh
./scripts/start_worker_cluster_300.sh
```

The 300 profile is meant for staging or a well-provisioned production worker node, not for the public/admin web processes. It requires Postgres, Redis, object storage, and external AI/API quota to be sized accordingly.

## 300-Concurrency Staging Profile

A non-secret environment template lives at:

- `deploy/env/viltrox-2.0.staging-300.example`

Use it as a starting point only:

```bash
sudo mkdir -p /etc/viltrox
sudo cp deploy/env/viltrox-2.0.staging-300.example /etc/viltrox/viltrox-2.0.env
sudo vi /etc/viltrox/viltrox-2.0.env
```

Replace all `CHANGE_ME` values before starting services. The template sets:

- `ENVIRONMENT=staging`
- mandatory `Postgres` and `Redis`
- `WORKER_CLUSTER_TIER=300`
- `20 x 15 = 300` logical async job consumers
- queue backpressure at soft `3000`, hard `10000`

Expected behavior under load:

- If the queue is healthy, upload/audit/link submissions enqueue normally.
- If waiting jobs exceed the soft limit, responses include queue pressure metadata.
- If waiting jobs exceed the hard limit, mutation endpoints return HTTP `429` with `Retry-After` instead of crashing the stack.

For a real 300-video workload, direct-to-object-storage upload and CDN/HLS playback should be enabled before public traffic. Worker concurrency alone does not make video upload/playback smooth.

## Load Test Automation

- Workflow: `.github/workflows/loadtest-smoke.yml`
- CI smoke runner: `scripts/run_loadtest_smoke_ci.sh`
- Core ramp script: `scripts/load_test_ramp.py`

The smoke workflow runs every 6 hours and on manual trigger, boots a local API process, executes a reduced ramp profile, then uploads JSON reports as workflow artifacts.

## systemd

Production systemd unit files live under:

- `deploy/systemd/viltrox-2.0-public.service`
- `deploy/systemd/viltrox-2.0-admin.service`
- `deploy/systemd/viltrox-2.0-worker.service`

They expect an environment file at:

- `/etc/viltrox/viltrox-2.0.env`

See `deploy/systemd/README.md` for install steps.

## Nginx + SSL

Nginx templates live under:

- `deploy/nginx/viltrox-2.0.conf`
- `deploy/nginx/README.md`

Recommended hostnames:

- `lab.viltrox.com` -> `127.0.0.1:8101`
- `admin.viltrox.com` -> `127.0.0.1:8102`

The template already includes:

- HTTP -> HTTPS redirect
- TLS termination placeholders
- SSE-friendly proxy config for `/api/audit/stream/*`
- SSE-friendly proxy config for `/api/via/sessions/*`
- upload-specific no-buffer proxying for `/api/upload/video`
- `client_max_body_size 550m` on the public host for video uploads
- static asset cache headers for `/assets/*`

Suggested rollout:

1. Install Nginx and Certbot.
2. Copy `deploy/nginx/viltrox-2.0.conf` into `/etc/nginx/sites-available/`.
3. Replace the placeholder domains and certificate paths.
4. Symlink into `/etc/nginx/sites-enabled/`.
5. Run `nginx -t`.
6. Reload Nginx.
7. Only then point public DNS at the server.

Example commands:

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx
sudo cp deploy/nginx/viltrox-2.0.conf /etc/nginx/sites-available/viltrox-2.0.conf
sudo ln -sf /etc/nginx/sites-available/viltrox-2.0.conf /etc/nginx/sites-enabled/viltrox-2.0.conf
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d lab.viltrox.com -d admin.viltrox.com
sudo systemctl enable nginx
```

Renewal check:

```bash
sudo certbot renew --dry-run
```

## Operations Runbook

Operational incident handling is documented in:

- `docs/OPERATIONS_RUNBOOK.md`

## Local HTTPS Proxy

For local end-to-end testing, the repo also includes a self-signed HTTPS front door:

- `deploy/nginx/viltrox-2.0.local.conf`
- `scripts/generate_local_ssl.sh`
- `scripts/start_local_https_proxy.sh`
- `scripts/stop_local_https_proxy.sh`

This local mode keeps the app roles on:

- `127.0.0.1:8101` public
- `127.0.0.1:8102` admin

And exposes HTTPS on:

- `https://localhost:8443`
- `https://localhost:9443`

Usage:

```bash
cd /Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0
source ./scripts/runtime_env.sh
APP_ROLE=public-web ./scripts/start_public.sh
APP_ROLE=admin-web ./scripts/start_admin.sh
./scripts/start_local_https_proxy.sh
```

The local proxy prefers the vendored binary at `runtime/vendor/nginx/sbin/nginx` and falls back to a system `nginx` install if present. If neither exists, the helper exits with a clear message. The certificate is generated automatically under `runtime/nginx/certs/` using `openssl`.

## Health Checks

Expected health endpoints:

- `http://127.0.0.1:8101/health`
- `http://127.0.0.1:8102/health`

Healthy responses should report:

- `database_backend=postgres`
- pool health
- `queue_backend=redis-stream`
- Redis event backend
- role identity

## Smoke Validation

Run the full backend smoke pass:

```bash
cd /Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0
./scripts/run_backend_smokes.sh
```

This validates:

- auth / social binding / student flow
- upload / audit / video factory flow
- Via runtime flow

## Deployment Checklist

Before external traffic:

1. Start Postgres and Redis.
2. Export the required environment.
3. Start `public-web`, `admin-web`, and `worker`.
4. Enable Nginx + TLS and verify the public/admin hostnames.
5. Verify both health endpoints.
6. Run backend smoke validation.
7. Confirm worker queue consumption and SSE event flow.

## Post-Deploy Validation

Run this sequence after every production or staging deploy:

1. `curl -fsS https://lab.viltrox.com/health`
2. `curl -fsS https://admin.viltrox.com/health`
3. Login through the public site with a creator account.
4. Upload a sample video and confirm `/api/upload/video` succeeds.
5. Submit an audit request and confirm the task enters the queue.
6. Open a Via session and confirm one successful reply turn.
7. Load the admin page and confirm submissions/rewards panels render.
8. Watch worker logs for stream consumption and confirm no pool timeout burst.

## Operational Notes

- `public-web` and `admin-web` use Gunicorn and should not run heavy background jobs.
- `worker` is the only role that should consume audit/video factory jobs.
- Redis Streams is the system of record for live queue execution.
- Postgres stores durable business data and job lifecycle history.
- The smoke scripts are safe to rerun because they use unique identifiers and fallbacks for rate limits / existing assets.
