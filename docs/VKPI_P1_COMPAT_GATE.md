# V-KPI P1 Compatibility Gate

P1 packages must pass this compatibility gate before feature code is installed.

## Why This Exists

The P1 package set was generated against a slightly different app shape:

1. P1 routers use `require_permission(...)`, while current V-KPI uses tab-level RBAC.
2. P1 routers used `/api/vkpi/...`, while admin V-KPI routes should use `/api/admin/vkpi/...`.
3. P1.3 comments code expects `external_post_id` and `raw_data_json`, while current `vkpi_industry_posts` uses `platform_post_id` and `raw_platform_data`.
4. P1.6 assumes `vkpi_staff`, while the current user/staff table is `staff`.

## Current Compatibility Contract

1. Use `app.api.dependencies.perms.require_permission` for P1 routers.
2. Use `app.services.vkpi.p1_compat.admin_router_prefix("comments")` or the explicit prefix `/api/admin/vkpi/comments`.
3. Use `app.services.vkpi.p1_compat.resolve_post_for_comments(...)` instead of directly selecting `external_post_id`.
4. Do not run P1.6 migration until it is rewritten against the current `staff` table.

## Acceptance Check

Run:

```bash
./scripts/run_smoke.sh smoke_vkpi_p1_compat.py
./scripts/run_smoke.sh --all
```

Expected after this gate:

1. P1 compat smoke passes.
2. Existing full smoke remains green.
3. No Reddit/Facebook/comments/sentiment/pillar/weekly-report feature code is installed yet.

## P1 Execution Order After This Gate

1. P1.1 Reddit offline registration only.
2. P1.2 Facebook offline registration only.
3. P1.3 Comments schema and collector, rewritten to use `p1_compat`.
4. P1.4 Sentiment after comments table is stable.
5. P1.5 Pillars after post/comment source data is stable.
6. P1.6 Weekly reports after staff schema alignment.
