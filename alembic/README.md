# Alembic migration bridge

This project keeps the existing SQL migration files in [`/migrations`](../migrations).

Alembic is introduced as a migration orchestrator so environments can run:

```bash
alembic -c alembic.ini upgrade head
```

Current baseline revision:
- `20260428_0001` (`bridge_existing_sql_migrations`)
- Applies the existing Postgres SQL sequence and records each file in `schema_migrations`.

Notes:
- The bridge revision is **Postgres-only**.
- Downgrade is intentionally disabled; use snapshot restore for rollback.
