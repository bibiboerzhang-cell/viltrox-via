# V-KPI Refactor Stop Gate

This document exists to prevent endless refactoring rounds.

## Current Decision

D-series KOL Ops splitting can pause after D8 unless a real blocker appears.

The goal is no longer "split every large file". The goal is:

1. Public API paths are locked by smoke.
2. High-risk schema, helper, dashboard, and content groups are isolated.
3. The remaining main router is readable enough for feature work.
4. Full smoke stays green.

## Stop Conditions

Stop splitting the current KOL Ops router when all are true:

1. `scripts/smoke_vkpi_kol_ops_route_surface.py` passes.
2. `./scripts/run_smoke.sh --all` passes.
3. `backend/app/api/routers/kol_ops.py` is below 900 lines.
4. No route group has a concrete bug, ownership conflict, or active feature need.

As of D8:

1. Route count is locked at 31.
2. `kol_ops.py` is 816 lines.
3. Schema, helpers, dashboard, and content routes are split.
4. Full smoke passed 44/44 before this gate was added.

## Continue Conditions

Only continue D-series splitting if at least one is true:

1. A real bug requires moving code to isolate ownership.
2. A team member needs a bounded write area to avoid conflicts.
3. A new feature would otherwise touch more than one unrelated route group.
4. A smoke failure proves route registration or import coupling is fragile.

## Next Useful Work

Prefer product-facing work over more splitting:

1. Finish remaining crawler mapping validation.
2. Verify budget and platform gates from the Settings UI.
3. Add manual KOL/product selection flows where the user already identified gaps.
4. Keep D-series only as a support track, not the primary roadmap.
