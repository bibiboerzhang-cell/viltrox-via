# V-KPI P4.1 Real Team Observation Readiness

Date: 2026-05-14

## Scope

P4.1 starts the post-P3 internal observation phase.

This round does not add Socialinsider-style analytics features. It prepares a
repeatable gate for real staff usage:

- runtime identity is current;
- feedback submission works from a staff token;
- feedback admin triage works from an admin token;
- active staff count is visible before the observation window starts;
- the team has a fixed checklist for real usage feedback.

## Non-Goals

- No new dashboard tabs.
- No new crawler or LLM provider work.
- No new project/KOL workflow behavior.
- No broad P3 cleanup.

## Readiness Gate

Run:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
./scripts/run_smoke.sh smoke_vkpi_p4_1_observation_readiness.py
```

Before the observation window, run the staff hygiene check:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_staff_observation_hygiene.py
```

If the printed rows are all stale smoke accounts, clean them:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_staff_observation_hygiene.py --apply
```

## Staff Provisioning Gate

P4.1B cleaned stale smoke staff. P4.1C adds a safe way to enable real employee
accounts for observation.

The provisioning script is dry-run by default:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
PYTHONPATH=backend .venv/bin/python scripts/vkpi_provision_observation_staff.py \
  --staff wanghua@viltrox.com,"Wang Hua",employee
```

For real Viltrox employees, use `@viltrox.com` emails. Non-Viltrox emails are
blocked unless `--allow-external` is explicitly passed.

Recommended CSV format:

```csv
email,name,role
wanghua@viltrox.com,Wang Hua,employee
wangshaoyuan@viltrox.com,Wang Shaoyuan,employee
marketing.manager@viltrox.com,Marketing Manager,manager
```

Dry-run first:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_provision_observation_staff.py \
  --csv /path/to/observation_staff.csv
```

Apply only after review. Put the temporary password in an environment variable;
do not write it into docs or commit it:

```bash
VKPI_OBSERVATION_DEFAULT_PASSWORD='change-this-in-private' \
PYTHONPATH=backend .venv/bin/python scripts/vkpi_provision_observation_staff.py \
  --csv /path/to/observation_staff.csv \
  --apply
```

If an existing user must be reset, add `--reset-password`. Otherwise existing
users keep their current password.

Provisioning smoke:

```bash
./scripts/run_smoke.sh smoke_vkpi_p4_1c_staff_provisioning.py
```

The smoke creates three temporary `@viltrox.com` staff accounts, verifies real
`/api/auth/login` and `/api/auth/me`, and cleans the rows.

The smoke verifies:

- `/health` returns a known `git_sha`;
- if `frontend/dist/build-info.json` exists, `client_matches_server=true`;
- `vkpi_team_feedback` schema exists;
- a staff user can submit feedback through `POST /api/admin/vkpi/feedback`;
- an admin user can list and triage feedback through `GET/PATCH /feedback`;
- feedback create/update audit records are written;
- active staff and admin staff counts are reported.
- P4.1C provisioning smoke verifies real login for manager and employee roles.

## Readiness vs Completion

P4.1 readiness is complete when the smoke passes.

P4.1 observation is complete only after real employees use the system.

Recommended minimum:

- at least 3 real active staff accounts;
- at least 1 admin or owner account;
- each tester submits at least 1 feedback item through the app;
- admin triages every item into one of:
  - blocker;
  - workflow friction;
  - enhancement;
  - data issue.

## Real Staff Test Script

Each tester should perform this path:

1. Login with their own account.
2. Open `红人搜索`.
3. Search or open one KOL.
4. Open `项目跟进`.
5. Open or create one project if their role allows it.
6. Open `数据分析`.
7. Open one account detail and one media item if available.
8. Submit feedback for one real issue or one confusing workflow.

Do not ask testers to use shared passwords.

## Admin Triage Script

Admin should perform this path:

1. Login as admin or owner.
2. Open the feedback admin path.
3. Filter open feedback.
4. Set at least one item to `triaged`.
5. Classify every issue outside the app in the P4 observation log.

## Exit Criteria

P4.1 can move to P4.2 when:

- readiness smoke passes;
- stale smoke staff rows are cleaned or explicitly explained;
- staff provisioning smoke passes;
- at least one admin triage flow is verified;
- real staff account count is known;
- the P4 observation log has at least one real user entry or an explicit note
  that employee account provisioning is still pending.

If fewer than 3 staff accounts are active, P4.1 code readiness can still be
closed, but P4.1 observation remains pending.
