# V-KPI P5 Budget Monitor UI

## Scope

P5 adds a manager-only Budget Guard monitor to the existing cost desk.

It uses existing backend routes:

```text
GET  /api/admin/vkpi/budgets
POST /api/admin/vkpi/budgets/{scope}/update
GET  /api/admin/vkpi/budgets/usage-by-provider
GET  /api/admin/vkpi/budgets/usage-by-cron
```

It does not:

```text
change LLM gateway behavior
add provider calls
write recommendation rows
write cost ledger rows
create new migrations
```

## UI

The manager cost page now shows:

```text
Budget Guard summary
scope threshold editor
provider/cron budget table
AI cost ledger usage by provider
AI cost ledger usage by cron
```

Employee view remains blocked from the cost desk.

## Acceptance Gates

```text
1. Frontend build passes.
2. Budget status loads from /api/admin/vkpi/budgets.
3. Provider and cron usage tables tolerate empty cost ledger data.
4. Editing a scope posts to /budgets/{scope}/update.
5. No backend route or migration changes are introduced.
6. No AI provider call is made by the UI.
```
