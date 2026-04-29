# Viltrox 2.0 Operations Runbook

Last updated: 2026-04-28

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
