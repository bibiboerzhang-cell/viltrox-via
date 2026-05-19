# V-KPI P6-3 Content Brain Budget Scope

## Scope

P6-3 seeds the Budget Guard scope used by content brain analysis:

```text
cron:p6_content_brain_analysis
```

It does not:

```text
enable provider calls
write AI cost ledger rows
change P6-2 dry-run behavior
write post/media analysis fields
```

## Migration

```text
migrations/063_vkpi_content_brain_budget_scope.sql
migrations/063_vkpi_content_brain_budget_scope_down.sql
```

Seeded cap:

```text
scope=cron:p6_content_brain_analysis
cap_usd=20.00
warning_at=0.80
hard_stop_at=1.00
fallback_action=fallback_to_rule_v0
```

## Acceptance Gates

```text
1. 063 and 063 down migrations exist.
2. 063 is registered in _POSTGRES_MIGRATION_SEQUENCE.
3. The seeded row uses ON CONFLICT DO NOTHING.
4. Down migration deletes only rows seeded by 063.
5. P6-2 still reports provider_calls=false.
```
