# V-KPI Apify Windowed Live Smoke Runbook

Use this runbook only after P1.X.A tier selection is initialized and reviewed.
It is for bounded live smoke tests of the qualified KOL Apify batch executor.
It must not be used to accelerate the old legacy full KOL refresh path.

## Current Boundary

- Default CLI mode is plan only.
- Real provider calls require both `--execute` and `--allow-provider-calls`.
- Live execution is capped by `--max-live-targets`, default `25`, hard max `100`.
- If the full qualified plan is larger than the live cap, execute one
  `--live-window-index` at a time.
- `vkpi-sync-daily.service` must remain official-only with `--skip-kol`.
- `vkpi-qualified-kol-refresh.timer` must not be enabled for live smoke.

## Backup First

Before any live provider smoke:

```bash
scripts/ops/backup_prod_vkpi.sh
```

Keep the backup path in the smoke report.

## Preflight Without Provider Calls

Run this first. It writes an artifact and does not call Apify.

```bash
ssh viltrox 'cd /opt/viltrox-2.0 && \
  PYTHONPATH=backend .venv/bin/python scripts/vkpi_apify_batch_refresh.py \
    --limit 120 \
    --tiers hot \
    --stale-before 2100-01-01T00:00:00Z \
    --chunk-sizes instagram=25,youtube=25 \
    --compact \
    --json-out runtime/ops/apify-windowed-preflight.json'
```

Acceptance:

- `provider_calls_allowed=false`
- `provider_gate.reason=provider_calls_not_requested`
- `execution_preflight.status=requires_windowed_execution` for the current full
  hot plan, or `ready` if the selected target count is within the live cap.
- `safe_live_windows.oversized_batch_count=0`
- `window_execution_runbook.available=true`
- `window_execution_runbook.execute_commands` contains one command per safe
  window.

## Select One Window Without Provider Calls

Use this to prove the window selector before live execution:

```bash
ssh viltrox 'cd /opt/viltrox-2.0 && \
  PYTHONPATH=backend .venv/bin/python scripts/vkpi_apify_batch_refresh.py \
    --limit 120 \
    --tiers hot \
    --stale-before 2100-01-01T00:00:00Z \
    --chunk-sizes instagram=25,youtube=25 \
    --live-window-index 1 \
    --compact \
    --json-out runtime/ops/apify-window-1-selection.json'
```

Acceptance:

- `provider_calls_allowed=false`
- `window_selection.selected=true`
- `operator_summary.target_count <= 25`
- `execution_preflight.status=ready`

## Live Smoke

Only run a command from `window_execution_runbook.execute_commands` after
operator approval for provider calls.

Recommended first live smoke is the smallest safe window, usually window `1`:

```bash
ssh viltrox 'cd /opt/viltrox-2.0 && \
  PYTHONPATH=backend .venv/bin/python scripts/vkpi_apify_batch_refresh.py \
    --limit 120 \
    --tiers hot \
    --stale-before 2100-01-01T00:00:00Z \
    --chunk-sizes instagram=25,youtube=25 \
    --live-window-index 1 \
    --execute \
    --allow-provider-calls \
    --compact \
    --json-out runtime/ops/apify-window-1-live-smoke.json'
```

Stop immediately if:

- any batch has `provider_status=error`
- `execution.summary.retry_count >= 3`
- the dataset cannot map items back to `kol_pool_id`
- Apify cost or account concurrency looks unexpected

## Post-Smoke Checks

```bash
curl -fsS https://viltroxtest.com/health
ssh viltrox 'systemctl is-active vkpi-sync-daily.service'
ssh viltrox 'systemctl is-enabled vkpi-qualified-kol-refresh.timer 2>/dev/null || true'
ssh viltrox 'systemctl cat vkpi-sync-daily.service | grep ExecStart'
```

Acceptance:

- `/health` is `ok` and client/server hashes match.
- `vkpi-sync-daily.service` is not stuck active.
- `vkpi-qualified-kol-refresh.timer` is `not-found` or disabled.
- `vkpi-sync-daily.service` still contains `--skip-kol`.

## Report Fields

Every smoke report must include:

- backup path
- artifact path
- commit hash
- `provider_calls_allowed`
- `provider_gate.reason`
- `window_selection`
- `operator_summary.target_count`
- `operator_summary.batch_count`
- `execution.summary.matched_items`
- `execution.summary.unmatched_items`
- `execution.summary.retry_count`
- `execution.failed_batches`

