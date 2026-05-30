# V615 Stabilization + Projects Merge Plan

Date: 2026-05-27
Branch: codex/dashboard-real

## Current Truth

- MY KOL and V615 shell are active in the frontend.
- Official account matrix has real API backing and frontend cache.
- KOL Pool row click no-white-screen smoke passed after stabilizing table row keys; detail drawer still needs V615 fidelity repairs.
- V615 shell now routes `projects` to the existing real Projects page instead of silently falling back to Dashboard.
- Projects is not a blank module. The repo already has `vkpi_projects`, project CRUD, stage transition, content, terms, shipment, costs, messages, and `vkpi_llm_calls`.
- The downloaded Projects deliverable contains useful UI, state-machine, schema, and integration code, but it must be merged into the existing module rather than copied as a parallel system.

## Immediate Performance Stabilization

Goal: page opens fast and never drops to empty data just because an API is slow.

1. Keep stale frontend cache visible for:
   - MY KOL official matrix
   - employee KOL content rows
   - V615 dashboard runtime bundle
   - KOL Pool rows
2. Use background refresh instead of blocking first paint.
3. Cap external media proxy timeout so broken upstream images do not occupy backend workers.
4. Add a local map data pack for dashboard map distribution once the city-level data contract is stable.
5. Keep missing data as `待接入 / 无信号 / 数据不足`, never fake zero.

## MY KOL / KOL Pool UI Completion

1. MY KOL team matrix
   - Manager view must show four staff cards per page. Current V615 smoke shows 4 staff cards and pager.
   - Use staff-directory fallback so pending/no-account staff remain visible.
   - Add pagination for more staff.
2. Employee KOL library
   - Left KOL list stays compact. Current CSS pass reduced row/avatar/card proportions for high-KOL lists.
   - All tab means "all Viltrox-related content for this KOL", not all platform noise. Current content layer filters to Viltrox rows.
   - Keep platform filters as secondary filters only.
   - Restore seven-stage cooperation funnel. Current V615 smoke shows 7 funnel stages.
3. Media
   - Use the same lightbox for video and image.
   - Prefer cached/R2 media URLs when available.
   - Show views, likes, comments, shares in the card and modal.
   - Comment count opens comment detail when comment cache exists.
4. KOL Pool
   - Restore V615 drawer detail shape: dimensions radar, fit breakdown, geo distribution, products, risks, representative works.
   - Fix white-screen regressions on click.
   - Do not route through stale V1 pages.

## Dashboard Data Correctness

1. Add snapshot maturity fields to dashboard responses:
   - `snapshot_days`
   - `required_days = 30`
   - `maturity_label`
2. Until snapshots mature, UI shows `真实 · 累积中 N/30`.
3. Do not label lifetime aggregates as last-30-days.
4. Preserve scope isolation:
   - owned
   - kol
   - all, clearly labeled as `K + O`
5. KOL dashboard aggregation requires visible KOL rule:
   - signed or pending trial
   - has real Viltrox product content
   - posted timestamp exists

## Projects Merge Strategy

Existing backend already has:

- `vkpi_projects`
- `vkpi_project_stage_events`
- `vkpi_messages`
- `vkpi_content_posts`
- `vkpi_content_assets`
- `vkpi_project_terms`
- `vkpi_sample_assets`
- `vkpi_shipments`
- `vkpi_llm_calls`

Deliverable tables missing locally:

- `project_kols`
- `stage_evidence`
- `project_assets`
- `shipping_tasks`
- `kol_contracts`
- `apify_jobs`
- `attribution_orders`
- `llm_calls` (repo uses `vkpi_llm_calls` instead)
- `v_project_dashboard`

Merge rule:

- Do not create a parallel `projects` table.
- Map deliverable `projects` fields onto existing `vkpi_projects`.
- Add only missing relation/evidence/job tables after migration review.
- Prefer existing V-KPI route prefix and service patterns.

## Projects Execution Order

1. Route restoration
   - Completed: `?v615=projects` now lazy-loads existing `ProjectsPage`.
   - Confirmed by browser smoke: Projects view appears, not Dashboard and not placeholder.
2. Recon and mapping
   - Compare deliverable schema/API/state-machine with existing project domain.
   - Produce exact table and endpoint mapping before migrations.
3. Frontend UI transplant
   - Move useful deliverable UI pieces into existing Projects page.
   - Do not replace existing real API layer.
4. Seven/nine-stage model alignment
   - Existing stage names stay compatible.
   - Add KOL-level timeline if current project-level stage is insufficient.
5. Schema migration
   - Add missing KOL-stage evidence/job tables only.
   - Use Alembic/repo schema helper; no raw production SQL.
6. API endpoints
   - Project KOL add/list/advance/branch.
   - Evidence upload/read.
   - Timeline/detail aggregation.
7. Integrations
   - LLM analysis through existing gateway and `vkpi_llm_calls`.
   - Apify jobs queued, not run synchronously.
   - Shipping tracking queued with provider-specific worker.
   - Shopify attribution uses existing attribution domain where possible.

## Latest Smoke Results

- `npm run build`: passed. Existing warning remains in intelligence chunk circular re-export; V615 Projects lazy import no longer forces Projects into the main chunk.
- MY KOL: 4 staff cards, 6 official platform cards, 7 employee platform filters, 7 funnel buttons, no visible `Not Found`/timeout error.
- KOL Pool: 1023 table rows loaded; clicking the first row opens the drawer signals (`V6 Fit`, `加入我的列表`, `深度评估`) without blanking the page.
- Projects: `?v615=projects#v615Replica` renders the existing Projects/Campaign OS surface, not Dashboard fallback.

## LLM / Apify / Provider Guard

Turn on in this order:

1. Dry-run and write-audit only.
2. Queue creation without external provider calls.
3. One-record smoke with explicit operator approval.
4. Batch run with budget and rate-limit guard.
5. Scheduled runs only after failure table and retry policy exist.

Never run these during UI-only stabilization:

- deep scan
- provider sync
- bulk Apify
- Gemini/LLM batch
- deployment

## Verification Gates

Every round must include:

- frontend build
- targeted backend compile/test if backend touched
- browser smoke for active page
- changed files list
- remaining backend gaps
