# V-KPI Sync Guard Ack Runbook

Use this runbook when `vkpi_sync_runs` contains an `interrupted` or `failed` daily sync that blocks the next run.

## Current Strategy-Change Ack

The current blocking run is expected to be:

```text
daily_incremental_sync_kol_pool_light_20260522T232155Z_8fba980b
```

This was a manual stop after the refresh policy changed. It is not a database crash recovery.

Required ack reason:

```text
Strategy change acknowledged: legacy full KOL pool daily refresh was stopped intentionally; future daily sync must skip legacy KOL pool until P1.X.A tier selector is deployed.
```

Backup first:

```bash
scripts/ops/backup_prod_vkpi.sh
```

Then ack:

```bash
ssh viltrox 'cd /opt/viltrox-2.0 && PYTHONPATH=backend .venv/bin/python scripts/vkpi_sync_ack.py \
  --target-run-id daily_incremental_sync_kol_pool_light_20260522T232155Z_8fba980b \
  --ack-by codex \
  --reason "Strategy change acknowledged: legacy full KOL pool daily refresh was stopped intentionally; future daily sync must skip legacy KOL pool until P1.X.A tier selector is deployed." \
  --check'
```

Acceptance:

- The returned guard check has `allowed=true`.
- Settings shows latest ack with the reason above.
- `vkpi-sync-daily.timer` is not re-enabled until the deployed daily sync skips legacy KOL refresh by default.
