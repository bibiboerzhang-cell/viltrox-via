# V-KPI P4-14 Recommendation Run Review UI

## Scope

P4-14 adds a read-only frontend review surface for persisted P4 preview runs.

It reads:

```text
GET /api/admin/vkpi/product-analysis/recommendation-runs
GET /api/admin/vkpi/product-analysis/recommendations?run_id=<id>
```

It does not:

```text
run recommendations
call LLM providers
write recommendation actions
assign tasks
transition projects
write frontend-only fake rows
```

## UI Behavior

The Product Analysis / Product Battle recommendation panel now includes a `推荐 Preview Run` table.

Supported strategy filters:

```text
new_launch_match_v1
kol_product_fit_v1
project_next_action_v1
```

Selecting a run loads its persisted recommendations into the existing candidate table. While a preview run is active, the candidate table enters read-only mode and replaces action buttons with `Preview only`.

## Acceptance Gates

```text
1. Frontend build passes.
2. The run table loads persisted runs through the existing API.
3. Selecting a run loads recommendations by run_id.
4. No new backend endpoint is introduced.
5. No recommendation action is exposed while reviewing a preview run.
6. P4.14 does not change migrations or service scoring logic.
```
