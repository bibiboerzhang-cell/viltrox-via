# V-KPI Backup and Restore Runbook

This runbook is the operator path for P3.15B. It covers what must be backed up,
what must never be included in a team package, and how to prove a restore can
work before V-KPI is handed to a wider internal team.

## Scope

Back up these assets:

- Postgres database: schema and data for application tables.
- Uploaded business files: project attachments, evidence files, invoices, and
  future KOL communication attachments.
- Source release package: code-only clean package with build metadata.
- Operations metadata: release notes, build SHA, and restore manifest.

Do not back up these into shareable code packages:

- `.env` and any `.env.*` files.
- `.git`, `.venv`, `node_modules`, and `frontend/dist`.
- `runtime/logs`, `runtime/backups`, temporary caches, and local DB files.
- Raw API keys, OAuth secrets, cookies, or browser session material.

## Daily Backup Command

Run from the repository root after loading the local runtime environment.

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
source scripts/runtime_env.sh

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="/Users/bibiboer/Documents/V-KPI-backups/db"
mkdir -p "$OUT_DIR"

pg_dump \
  --format=custom \
  --no-owner \
  --no-privileges \
  --file="$OUT_DIR/vkpi-db-$STAMP.dump" \
  "$DATABASE_URL"
```

Expected result:

- `vkpi-db-*.dump` exists.
- File size is non-zero.
- The command does not print or store secrets.

## Attachment Backup

If `uploads/` or project attachment folders are enabled, copy them into a
separate attachment backup. Keep this separate from source packages.

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="/Users/bibiboer/Documents/V-KPI-backups/attachments"
mkdir -p "$OUT_DIR"

tar czf "$OUT_DIR/vkpi-attachments-$STAMP.tar.gz" \
  --exclude='*.tmp' \
  uploads runtime/uploads 2>/dev/null || true
```

## Clean Source Package

Use the existing clean package script for code handoff. It refuses dirty
worktrees by default and scans for forbidden folders and common secret patterns.

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
bash scripts/make_vkpi_clean_package.sh
```

For local QA snapshots only, `ALLOW_DIRTY=1` can be used. Do not use dirty
packages for team release unless the diff is intentionally documented.

## Restore Drill

Run restore drills into a temporary database, not the active production/local
database.

```bash
createdb vkpi_restore_drill
pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --dbname="postgresql:///vkpi_restore_drill" \
  /path/to/vkpi-db-YYYYMMDD-HHMMSS.dump
```

After restore:

```bash
psql "postgresql:///vkpi_restore_drill" -c "SELECT count(*) FROM staff;"
psql "postgresql:///vkpi_restore_drill" -c "SELECT count(*) FROM kols;"
psql "postgresql:///vkpi_restore_drill" -c "SELECT count(*) FROM vkpi_projects;"
```

Acceptance:

- Restore completes without SQL errors.
- Core tables exist.
- Row counts are plausible for the snapshot date.
- A temporary app instance can run health checks against the restored DB.

## Automated Readiness Smoke

P3.15B adds a smoke that does not dump business data. It performs a schema-only
database export, verifies backup scripts parse, confirms critical tables exist,
and validates a small archive/extract roundtrip.

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
./scripts/run_smoke.sh smoke_vkpi_p3_15b_backup_restore.py
```

Expected marker:

```text
VKPI_P3_15B_BACKUP_RESTORE_SMOKE_OK
```

## Operator Checklist

- Daily DB backup exists.
- Attachment backup exists if attachment uploads are enabled.
- Clean source package exists for handoff.
- Restore drill has been tested at least monthly.
- No backup archive contains `.env`, API keys, cookies, or local caches.
