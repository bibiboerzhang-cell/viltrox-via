# V-KPI P2.23 Browser QA: Settings / Data Analysis / Discover

Date: 2026-05-10

## Scope

P2.23 covers real browser QA for three user-facing surfaces:

- Settings
- Data Analysis
- Discover / KOL lookup

This round intentionally avoids new crawler spend and avoids broad D-series refactors. It only fixes issues observed in the browser QA pass.

## Browser Findings

### Settings

- Page opened from sidebar successfully.
- System settings, API status, feature flags, platform crawl controls, and budget sections rendered.
- No visible `500 Internal Server Error`.
- Browser console error count: `0`.

### Data Analysis

- Page opened from sidebar successfully.
- Data analysis hero, project/account controls, filters, KPI cards, matrix, and account detail tab surface rendered.
- No visible `500 Internal Server Error`.
- Browser console error count: `0`.

### Discover / KOL Lookup

- Page opened from sidebar successfully.
- Platform selector, handle input, auto-create checkbox, crawl checkbox, lookup action, existing KOL section, and result area rendered.
- No visible `500 Internal Server Error`.
- Browser console error count: `0`.

## Fix Applied

Browser QA found one cross-page drawer issue:

- A global right-side drawer could stay open after switching main sidebar pages.
- Example: opening a KOL/profile/project drawer, then navigating to Discover, left the drawer overlay mounted on top of the new page.

Fix:

- `VkpiDashboard` now routes sidebar page selection through `handleSelectPage` instead of raw `setActivePage`.
- `handleSelectPage` calls `closeWorkspaceDrawers()` after switching page.
- `closeWorkspaceDrawers()` clears evidence, project detail, KOL profile, staff profile, and alert detail drawers.

## Guard

New smoke:

```bash
./scripts/run_smoke.sh smoke_vkpi_p2_23_navigation_drawers_frontend.py
```

Expected marker:

```text
VKPI_P2_23_NAVIGATION_DRAWERS_FRONTEND_SMOKE_OK
```

## Non-goals

- No live platform crawl.
- No budget changes.
- No API key changes.
- No large component split.
- No broad Settings redesign.

## Next

P2.24 should cover budget and crawl control loop: Settings switch/budget -> Data Analysis refresh status -> account refresh affordance.
