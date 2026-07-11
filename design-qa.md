# Dashboard Layout, Glass, and Evidence Design QA

## Scope

- Canonical visual source: `/Users/bibiboer/Desktop/V-KPI-Dashboard.html`
- Local route: `http://localhost:5188/#cockpit`
- Verification date: `2026-07-10` (America/New_York)
- Audit captures: `docs/audits/dashboard_layout_glass_2026-07-10/`
- Scope: Dashboard/base chrome, editable module geometry, glass clarity, topbar popovers, KOL detail drawer, AI Today evidence, and Signals provenance.

## Implemented

- Replaced fixed span switching with a real 12-column two-dimensional grid. Modules drag by a dedicated top handle, resize from all four edges and all four corners, resolve collisions, and compact vertically.
- Added persisted layout schema v3 with `x`, `y`, `span`, and `height`; older layouts migrate without discarding existing user-added modules.
- Added `自动排列` for hole-free magnetic packing and responsive 12/6/1-column reflow. Geometry editing remains desktop-only while narrower views repack automatically.
- Added glass clarity from 60% to 96%, with a live range input plus precise minus/plus controls. Panel, overlay, and drawer opacity are separate tokens so the background remains visible without washing out text.
- Rebuilt notification/account popovers as viewport portals. Outside-click handling no longer places an intercepting full-screen layer over the topbar, so menus switch in one click.
- Strengthened the KOL detail drawer surface while retaining glass blur and the underlying page context.
- Kept the real geographic map and restored the reference hierarchy: KPI band, large command-center map, and growth-health card in the first viewport.

## Functional Verification

- Frontend full suite: 65 files, 390 tests passed. Existing unrelated asynchronous React `act(...)` warnings remain non-blocking.
- Focused layout/appearance/popover suite: 3 files, 11 tests passed.
- TypeScript and production build: passed; Vite transformed 2,973 modules.
- Local frontend: HTTP 200 on port 5188.
- Local backend: HTTP 200 on port 8102; server/client SHA `9bfcd4a9` aligned.
- Real drag: Growth Health moved from `x=0` to `x=3` and snapped to the grid.
- Real resize: Growth Health changed from `4x10` to `5x13`; the size survived a reload, then was returned to `4x10`.
- Auto arrangement: 25 active modules, zero geometric overlaps.
- Responsive: 1180px produced six columns; 540px produced one column; both had zero overlaps and zero horizontal overflow.
- Popovers: notification and account menus switch with one click while Dashboard remains visible.
- KOL drawer: visible at 520px width with `rgba(9, 14, 25, 0.88)` background.
- AI Today: four external market examples rendered with source links across Instagram and YouTube.
- Signals: the inspected Tamron record exposed three of three traceable Reddit source links and labeled the evidence as related historical examples rather than direct proof.
- Current Dashboard reload: no new console errors after the final verification window.

## Visual Verification

- Reference capture: `docs/audits/dashboard_layout_glass_2026-07-10/00-reference-dashboard-1600x900.png`
- Final implementation: `docs/audits/dashboard_layout_glass_2026-07-10/12-dashboard-qa-1600x900.png`
- Full comparison: `docs/audits/dashboard_layout_glass_2026-07-10/comparison-full.png`
- Focused comparison: `docs/audits/dashboard_layout_glass_2026-07-10/comparison-primary-modules.png`
- Interaction captures include edit handles, appearance controls, notification popover, KOL drawer, responsive layouts, AI Today evidence, and Signals traceability.
- The implementation intentionally keeps the real interactive world map instead of copying the demo's static network graphic.

## Residual Conditions

- Worker heartbeat is offline and the UI currently reports five queued jobs; this is runtime state, not hidden behind fake progress.
- Attributed GMV and average ROI remain pending Shopify order and cost inputs.
- The current competitor-radar item is labeled as a stale historical snapshot.
- Production build emits one 585.41 kB chunk warning; this is a later performance split, not a blocker for this interaction round.
- KOL detail inspection exposed a pre-existing React unique-key warning in `SafetyAuthenticityPanel`; no visible drawer failure occurred.

## 2026-07-11 KPI and Sidebar Follow-up

- Replaced decorative KPI rails with source-backed 30-day series for the company-account scope. Active roster, active 30d, exposure, and engagement now each expose 30 points, a first-to-last delta, coverage, source dates, and a visible endpoint.
- Reconciled card and series definitions. The verified latest values match exactly: active roster `18`, active 30d `17`, exposure `27,806,378`, and engagement `3.972386%`.
- Corrected Active 30D so positive post deltas are bounded to the rolling window instead of making an account permanently active. The previous displayed value `18` therefore became the defensible value `17`.
- Kept GMV and ROI in explicit pending states. Neither card receives a fake line, endpoint, or growth badge.
- Moved the six-column KPI breakpoint from `xl` to `2xl`; narrower desktop layouts use three columns before labels can collapse into vertical text. The production CSS contains the generated `2xl:grid-cols-6` rule.
- Separated the sidebar, workspace, topbar, and compact LLM queue surfaces through theme tokens. The sidebar keeps one right divider, the topbar no longer adds a second horizontal shadow line, and the queue card retains its own border, shadow, and inset highlight.
- Edit-mode boundaries are quiet at rest, dotted on hover/focus, and solid while dragging or resizing. The visible controls still expose magnetic reordering, edge/corner resize, auto-arrange, restore-default, and sidebar collapse/expand.
- Final local state verified as `glass + dark`, company-account scope, edit mode off, sidebar expanded.

### Follow-up Verification

- Backend Dashboard tests: 33 passed.
- Frontend focused Dashboard tests: 46 passed across seven files.
- TypeScript and production build: passed; Vite transformed 2,973 modules.
- `git diff --check`: passed.
- Gunicorn reloaded successfully; `/health` returned HTTP 200 and server/client SHA `9bfcd4a9` remained aligned.
- Visual evidence: `docs/audits/dashboard_kpi_llm_round_2026-07-11/02-dark-company-series.png`, `03-light-company-series.png`, `05-light-edit-layout.png`, and `07-compact-1280.png`.
- Reference/current comparison was reviewed against `docs/audits/dashboard_llm_kpi_diagnosis_2026-07-11/02-reference-light.png`; no text overlap, duplicated sidebar divider, static fake trend, missing endpoint, or unreadable KPI badge remained in the verified states.

final result: passed (no P0, P1, or P2 visual or interaction defects found in the verified Dashboard scope)
