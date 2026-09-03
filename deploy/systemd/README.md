# Viltrox 2.0 systemd units

## Legacy 3-role templates (not used by the atomic deploy)

These unit files are the original systemd wrappers for the 3-role runtime:

- `viltrox-2.0-public.service`
- `viltrox-2.0-admin.service`
- `viltrox-2.0-worker.service`
- `viltrox-2.0-scheduler.service`

They expect an environment file at:

- `/etc/viltrox/viltrox-2.0.env`

Suggested install flow:

```bash
sudo mkdir -p /etc/viltrox
sudo cp /path/to/viltrox-2.0.env /etc/viltrox/viltrox-2.0.env
sudo cp deploy/systemd/viltrox-2.0-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable viltrox-2.0-public.service
sudo systemctl enable viltrox-2.0-admin.service
sudo systemctl enable viltrox-2.0-worker.service
sudo systemctl start viltrox-2.0-public.service
sudo systemctl start viltrox-2.0-admin.service
sudo systemctl start viltrox-2.0-worker.service
```

Use a real service user instead of the template `%i` if you prefer a fixed runtime account.

**Production note.** `scripts/ops/deploy_local_to_cloud.sh` treats every
`viltrox-2.0-*.service` name above as a *legacy writer* that must be
`inactive`/`failed` before a release (`LEGACY_WRITER_UNITS`). Do not enable
them on the atomic-release host; the production fleet lives under
`scripts/ops/systemd/` (web `viltrox-2.0-test.service`, 1+15 apify lanes,
`vkpi-redis-worker.service`, sync/sentinel/backup timers).

## `vkpi-scheduler.service` — dedicated scheduler owner (A1 W1, default OFF)

Today the APScheduler runs inside the web gunicorn (`viltrox-2.0-test.service`,
`ENABLE_SCHEDULER=1`); migration 249's PostgreSQL advisory leader lock keeps it
single-owner across the two web processes. `vkpi-scheduler.service` moves that
owner into its own hardened unit so a web restart/blue-green switch never drops
scheduled work, and so the scheduler's connection budget is explicit.

Install list used by the deploy (only when the gate is on):

| Gate (controller env) | Effect during `deploy_local_to_cloud.sh` |
|---|---|
| unset / `0` (default) | Nothing changes. Unit is not installed, not enabled. Scheduler stays inside web. |
| `VKPI_DEPLOY_SEPARATE_SCHEDULER=1` | Installs `deploy/systemd/vkpi-scheduler.service` to `/etc/systemd/system/` atomically (tmp + cmp + rename, root:root 0644), `systemd-analyze verify`, `daemon-reload`, `enable`, `restart`, waits ≤30 s for `active`. |

The unit name is deliberately **not** `viltrox-2.0-scheduler.service`: that
name is on the legacy-writer deny list above and would fail the release gate.

Contract carried on the `ExecStart` argv (EnvironmentFile values override
`Environment=`, so the fixed values travel as argv assignments like the web and
lane units): `APP_ROLE=worker ENABLE_SCHEDULER=1 ENABLE_UPLOAD_CLEANUP=1
HOST=127.0.0.1 PORT=8103 WORKERS=1 DB_USE_PGBOUNCER=0 POSTGRES_POOL_MAX_SIZE=8`.
`ExecStartPre` refuses to start if `/opt/viltrox-2.0/.env.production` defines
`ENABLE_SCHEDULER|PORT|HOST|BIND|WORKERS|APP_ROLE` (that overlay is loaded in
override mode by `runtime_env.sh`).

Connection budget after the switch: lanes 16×6=96 + redis worker 16 + web
2×16=32 + scheduler 8 + leases/ops ≈14 ≈ 166 < PG `max_connections=200`.

### Switch-over (operator steps, one release)

1. Deploy once with `VKPI_DEPLOY_SEPARATE_SCHEDULER=1` exported on the
   controller (train.sh / deploy_local_to_cloud.sh inherit it). The gate block
   runs right after the reviewed unit install, before the web restart.
2. Verify: `systemctl is-active vkpi-scheduler.service`,
   `journalctl -u vkpi-scheduler --since -5m | grep scheduler.fleet_leader` —
   exactly one leader across web + scheduler (the loser logs a lease wait).
3. Web side, **separate owner-reviewed change** (not in this lane):
   `scripts/ops/systemd/viltrox-2.0-test.service` `Environment=ENABLE_SCHEDULER=0`
   and the `scripts/start_admin.sh` contract line 61
   (`export ENABLE_SCHEDULER=1` → `export ENABLE_SCHEDULER="${VKPI_WEB_ENABLE_SCHEDULER:-1}"`
   captured before `runtime_env.sh`). Until it lands both candidates coexist
   safely under the advisory lock; only one fires jobs.
4. Keep the gate exported for every later deploy. A deploy without it does not
   remove the unit; it simply stops managing it (the unit keeps running the
   `current` tree it was restarted on — restart it manually after that deploy).

### Rollback

```bash
sudo systemctl disable --now vkpi-scheduler.service
sudo rm -f /etc/systemd/system/vkpi-scheduler.service && sudo systemctl daemon-reload
# web still carries ENABLE_SCHEDULER=1 until step 3 above lands, so the
# in-web scheduler re-acquires the leader lock within one lease interval.
```

Known gap (documented, not fixed here): the deploy's quiesce step stops the
web + 16 lanes + redis worker but not this unit, so during a release the
dedicated scheduler keeps running the *previous* code until the gate block
restarts it. Adding it to `quiesce_remote_release_consumers` /
`JOURNAL_SYSTEMD_UNITS` is a deploy-lane follow-up.

`pgbouncer.service.example` is a reference only; the deploy manages the real
PgBouncer units through its own reviewed map.
