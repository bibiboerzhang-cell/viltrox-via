# Viltrox 2.0 Operations Runbook

Last updated: 2026-05-20

## 1. Incident levels

- `SEV-1`: Public submit/login/admin unavailable, or data loss risk.
- `SEV-2`: Core flow degraded (high latency, queue backlog, partial feature outage).
- `SEV-3`: Non-core feature degraded (reporting tabs, optional integrations).

## 2. First 5-minute triage

Run from project root:

```bash
cd /opt/viltrox-2.0
curl -fsS http://127.0.0.1:8000/health
```

If health fails:

```bash
sudo systemctl status viltrox-2.0 --no-pager
sudo journalctl -u viltrox-2.0 -n 200 --no-pager
```

If health passes but users report lag:

```bash
curl -fsS "http://127.0.0.1:8000/api/admin/runtime/queues" \
  -H "Authorization: Bearer <admin_token>"
```

Check:
- waiting
- processing
- failed
- avg_duration_ms

## 3. Common failure playbooks

### 3.1 API process down

1. Restart service:

```bash
sudo systemctl restart viltrox-2.0
```

2. Validate:

```bash
curl -fsS http://127.0.0.1:8000/health
```

3. If still failing, rollback (section 6).

### 3.2 Queue backlog rising

Symptoms:
- waiting count grows continuously
- completion ETA rising

Actions:
1. Increase worker cluster:

```bash
WORKER_CLUSTER_SIZE=120 bash ./scripts/start_worker_cluster.sh
# or
bash ./scripts/start_worker_cluster_300.sh
```

2. Verify queue drain speed via admin runtime queue endpoint every 60s.
3. If backlog remains high, enable backpressure mode (temporary):
   - tighten link submit acceptance thresholds
   - surface “queued” to clients instead of hard failing

### 3.3 Redis unavailable

```bash
redis-cli -u "$REDIS_URL" ping
sudo systemctl restart redis
```

Then restart app workers/web pods.

### 3.4 Postgres connection saturation

Symptoms:
- timeout from DB layer
- high wait in app logs

Actions:
1. Ensure pooler route is active:
   - `DB_USE_PGBOUNCER=1`
   - `DATABASE_POOL_URL` points to pgbouncer
2. Check pgbouncer:

```bash
sudo systemctl status pgbouncer --no-pager
```

3. If needed, temporarily reduce app worker count to protect DB.

### 3.5 Upload processing slow

Checklist:
- object storage reachable
- AI provider quota not exhausted
- worker cluster size sufficient
- transcode job failures not spiking

Quick check:

```bash
python scripts/smoke_real_video_upload_audit_300.py
```

## 4. Deployment preflight

Before deploy:

```bash
bash ./scripts/verify_repo.sh
```

For Postgres environments:

```bash
bash ./scripts/alembic_upgrade.sh
```

For load-test smoke:

```bash
bash ./scripts/run_loadtest_smoke_ci.sh
```

## 4.1 V-KPI production backup and local/cloud sync

Run these commands from the local repo root unless noted otherwise.

### Backup first

Before any production sync, baseline refill, data import, or deploy:

```bash
scripts/ops/backup_prod_vkpi.sh
```

This creates a remote backup under `/opt/viltrox-2.0/backups/ops/<UTC_STAMP>` and downloads a local copy under `runtime/prod-sync/<UTC_STAMP>`.

The backup includes:
- Postgres `pg_dump` custom-format DB dump.
- DB dump SHA256.
- `uploads/vkpi_media_cache` size and file-count snapshot.
- `uploads/vkpi_media_cache` file manifest.
- Runtime state, service status, and current frontend asset name.

### Optional R2 media cache

V-KPI official-account media cache is local by default. To let newly cached
official-account videos upload to Cloudflare R2 after local download, configure
these env vars and restart the app:

```bash
VKPI_MEDIA_CACHE_STORAGE=hybrid
VKPI_MEDIA_CACHE_R2_PREFIX=vkpi/media-cache
VKPI_MEDIA_CACHE_R2_PUBLIC_BASE_URL=https://<cdn-host>
R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=<key>
R2_SECRET_ACCESS_KEY=<secret>
R2_BUCKET_NAME=<bucket>
```

The fallback is deliberate: if R2 is not configured or upload fails, playback
continues through the local `/api/vkpi-media/video-cache/...` URL. Old local
cache migration remains a separate operation.

Dry-run old local video-cache migration first:

```bash
python scripts/migrate_vkpi_media_cache_to_r2.py --limit 200
```

Only after env is verified, execute in bounded batches:

```bash
VKPI_MEDIA_CACHE_STORAGE=hybrid \
python scripts/migrate_vkpi_media_cache_to_r2.py --execute --limit 200
```

Large media archive is off by default. Use it only when there is enough disk and transfer time:

```bash
BACKUP_MEDIA_ARCHIVE=1 scripts/ops/backup_prod_vkpi.sh
```

### Pull production snapshot to local

Use this to make the local machine hold the latest production snapshot without overwriting any local database:

```bash
scripts/ops/sync_prod_snapshot_to_local.sh
```

By default this downloads only. It does not restore into a local DB.
It also checks `vkpi-sync-daily.service` first and skips safely while the remote
daily sync is `active` or `activating`. Override only for an intentional ops run:

```bash
ALLOW_DURING_SYNC=1 scripts/ops/sync_prod_snapshot_to_local.sh
```

Restore into an explicit local Postgres QA database only with all guards set:

```bash
RESTORE_LOCAL=1 \
ALLOW_LOCAL_DB_RESTORE=1 \
LOCAL_DATABASE_URL='postgresql://...' \
scripts/ops/sync_prod_snapshot_to_local.sh
```

Do not restore into production, and do not treat this as automatic two-way DB sync. Production remains the source of truth for runtime data.

### Deploy local code to cloud

Use the guarded deploy script:

```bash
scripts/ops/deploy_local_to_cloud.sh
```

The deploy script refuses to run while `vkpi-sync-daily.service` is `active` or
`activating`, because it would rebuild, back up, rsync, and restart the app
during a data write. Override only for a deliberate incident run:

```bash
ALLOW_DURING_SYNC=1 scripts/ops/deploy_local_to_cloud.sh
```

The script:
- Refuses dirty worktrees unless `ALLOW_DIRTY_DEPLOY=1`.
- Builds frontend unless `SKIP_BUILD=1`.
- Takes a production backup unless `SKIP_BACKUP=1`.
- Rsyncs code while excluding `.git`, `.env`, virtualenvs, node modules, uploads, runtime files, backups, and local DB files.
- Restarts `viltrox-2.0-test.service`.
- Checks `/health`.
- Verifies the local and remote `app-*.js` asset names match.

Use `RSYNC_DELETE=1` only for an intentional clean package deploy after reviewing excluded paths.

### Run a production V-KPI job

Generic guarded runner:

```bash
JOB_NAME=official_full_baseline PAYLOAD_JSON='{}' scripts/ops/run_prod_vkpi_job.sh
```

This takes a backup by default, runs the job on `viltrox`, and writes a remote log under `/opt/viltrox-2.0/runtime/ops/`.

Current safe company-owned account baseline entrypoint:

```bash
JOB_NAME=official_full_baseline PAYLOAD_JSON='{}' scripts/ops/run_prod_vkpi_job.sh
```

Current state audit:

```bash
scripts/ops/audit_prod_vkpi_state.sh
```

Tonight batch wrapper:

```bash
scripts/ops/tonight_vkpi_data_run.sh
```

The wrapper runs a preflight audit, backs up production, runs `official_full_baseline`, then audits again. It intentionally does not start an unverified 1012 provider/deep-scan job. The current production-safe 1012 surface is `vkpi_kol_pool` state verification until a concrete provider job entrypoint is implemented and accepted.

### Current 2026-05-20 V-KPI sync gate

As of the 2026-05-20 state check:
- `vkpi_employee_channels`: 18 active official accounts.
- `vkpi_kol_pool`: 1023 total, 1012 from `legacy_excel_p2d`.
- Active queued/running V-KPI jobs: 0.
- 2026-05-20 official-channel metrics now have 18 rows, `posts_delta=43`, `views_delta=613067`, `followers_delta=4804`.
- Search can use the 1012-row historical KOL pool for existing/cooperation matches before platform live search returns.

Do not start a 1012 provider/deep-scan run from this wrapper. Keep historical KOL pool matching separate from provider refresh until the lightweight 1012 job entrypoint is implemented and accepted.

## 5. Secrets and key rotation

Use dual-key rollout pattern:

1. Put new key in primary variable, old key in `*_PREVIOUS`.
2. Deploy.
3. Observe for 24h.
4. Remove old key from `*_PREVIOUS`.

Supported dual-key vars:
- `JWT_SECRET` + `JWT_SECRET_PREVIOUS`
- `META_WEBHOOK_VERIFY_TOKEN` + `META_WEBHOOK_VERIFY_TOKEN_PREVIOUS`
- `META_APP_SECRET` + `META_APP_SECRET_PREVIOUS`
- `SHOPIFY_WEBHOOK_SECRET` + `SHOPIFY_WEBHOOK_SECRET_PREVIOUS`
- `TIKTOK_WEBHOOK_SECRET` + `TIKTOK_WEBHOOK_SECRET_PREVIOUS`
- `PLATFORM_INGEST_SHARED_SECRET` + `PLATFORM_INGEST_SHARED_SECRET_PREVIOUS`

## 6. Rollback

### 6.1 App rollback

1. Switch to previous release directory/symlink.
2. Restart service.
3. Validate `/health`, login, submit, admin read.

### 6.2 DB rollback

Use snapshot restore only (no generic downgrade path in migration bridge):

1. Stop writes.
2. Restore latest known-good snapshot.
3. Run verification smokes.
4. Resume traffic.

## 7. Minimal post-incident report

Record:
- start time / recovery time
- affected endpoints and user impact
- root cause
- corrective action
- prevention task with owner and deadline
