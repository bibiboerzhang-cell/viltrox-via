# V-KPI Code Hardening Audit Plan

## Current Round

This round implements the hardening plan as a test-first safety pass. The time schedule from the external proposal is intentionally ignored. Execution order is based on risk:

1. Lock P3/P4/P7/P10 behavior with integration tests.
2. Add a read-only hardening baseline script.
3. Extract only safe shared conversion helpers.
4. Defer large file splits until the tests protect the current behavior.

## Accepted

- Add P4-P12 hardening tests around real service entrypoints.
- Keep tests on the existing synchronous `get_conn()` compatibility layer.
- Verify P3 readiness before P4 recommendation tests.
- Verify P4 dry-run remains explainable and does not record AI cost.
- Verify P4 preview persistence writes run, recommendation, and explanation rows.
- Verify P10 recommendation actions write feedback and outcome labels.
- Verify P7 recommendation review-gap alerts open and clear after feedback.
- Verify DB pool status through the existing `probe_postgres_connectivity()` and `get_db_actor_stats()` helpers.
- Extract small shared helpers for JSON, UTC timestamps, row conversion, and numeric/text conversion.

## Corrected

- The proposal's async/asyncpg examples do not match the current codebase. V-KPI services in this path are synchronous and run through the repo's DB compatibility layer.
- The proposal's DB pool section is not implemented as a new pool. The repo already has `psycopg_pool.ConnectionPool` wiring and `POSTGRES_POOL_*` config in `backend/app/db/connection.py`.
- The proposal's large-file split is delayed. Tests land first; structural refactors follow only after the behavior is covered.
- P2D rollback remains a manual drill path. It is not added to default pytest because it can mutate real business tables.

## Deferred

- Split `backend/app/services/vkpi/memory.py`.
- Split `backend/app/api/routers/admin.py`.
- Convert sync services to async.
- Add FastAPI `BackgroundTasks` to long-running paths.
- Delete or rewrite indexes without production stats.
- Enforce a global zero `except Exception` rule.

## Test Commands

```bash
.venv/bin/python -m pytest tests/test_vkpi_p4_p12_hardening.py -q
.venv/bin/python -m pytest tests/test_vkpi_kol_pool.py tests/test_vkpi_metric_lineage.py -q
.venv/bin/python scripts/build_vkpi_memory.py --readiness
.venv/bin/python scripts/vkpi_code_hardening_baseline.py
```

## Red Lines

- P4 dry-run must not call providers.
- P4 dry-run must not add `vkpi_ai_cost_ledger` rows.
- P4 recommendations must include score breakdown and evidence.
- P10 actions must not duplicate shortlist feedback on repeated clicks.
- P7 review-gap alerts must clear after recommendation feedback.
- P3 readiness must stay `ready_for_p4_dry_run`.
- No default pytest may rollback or rewrite the real legacy import batch.
