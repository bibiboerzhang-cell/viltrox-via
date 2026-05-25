# V-KPI Backend to UI Handoff

Status: backend architecture ready for UI replacement

Date: 2026-05-25

## Current Backend Boundary

The backend has moved from flat `services/vkpi` ownership to the Domain OS layout:

| Layer | Current role | UI usage |
|---|---|---|
| `backend/app/api/routers` | HTTP boundary only | UI calls these endpoints, not domain modules directly |
| `backend/app/domains` | Business behavior | Source of dashboard, KOL, projects, attribution, market, intelligence, settings |
| `backend/app/platform` | Infrastructure and providers | DB schema guards, crawlers, LLM gateway |
| `backend/app/shared` | Cross-domain helpers | Shared evidence, utilities, decision helpers |
| `backend/app/services/vkpi` | Compatibility shims only | Do not use for new UI work |

Current checks:

| Check | Result |
|---|---:|
| Router imports from `app.services.vkpi` | 0 |
| Domain/shared/platform imports from `app.services.vkpi` | 0 |
| `services/vkpi` implementation size | 313 lines, shims only |
| Line guard | 0 violations at 800-line limit |

## UI-Ready Endpoint Groups

These endpoint groups are safe to use as the first source for the replacement UI:

| UI area | Router group | Domain owner | Notes |
|---|---|---|---|
| Management dashboard | `vkpi_dashboard_staff.py`, `vkpi.py` | `domains/dashboard`, `domains/staff` | Use for summary, performance, staff-aware views |
| KOL search and pool | `vkpi_search.py`, `vkpi_kol_pool.py` | `domains/kol` | New UI should distinguish official accounts, qualified KOL, and legacy cold records |
| Projects | `vkpi_projects.py`, `vkpi_tasks.py` | `domains/projects` | Project workflow remains the operational spine |
| Attribution, links, costs | `vkpi_attribution_metrics.py`, `vkpi_kol_links.py`, `vkpi_costs.py` | `domains/attribution`, `domains/costs` | Short-link, sales attribution, and cost views should be combined in UI |
| Market and intelligence | `vkpi_industry_market.py`, `vkpi_industry_automation.py`, `vkpi_learning.py` | `domains/market`, `domains/intelligence`, `domains/analytics` | Provider calls remain gated; do not show empty cards as smart output |
| Data quality | `vkpi_data_quality.py`, `vkpi_sync.py` | `domains/data_quality`, `domains/sync` | Use for backend health, sync status, and trust labels |
| Settings | `vkpi_settings.py`, `vkpi_access.py`, `vkpi_firewall.py` | `domains/settings`, `domains/access` | Settings UI should stay five-panel and hide diagnostics by default |
| Reports | `vkpi_reports.py`, `vkpi_weekly_reports.py` | `domains/reports` | Weekly report exists; monthly automation is not closed |

## Do Not Use as New UI Source

| Area | Why |
|---|---|
| `backend/app/services/vkpi/*` | Compatibility only. New code should import router/API clients or domain-owned backend modules. |
| `domains/legacy_import` | Historical Excel/staging/rollback tooling. Keep out of normal UI navigation. |
| Repair Center internals | Frozen governance tool. It should not become the main product workflow. |
| Provider smoke scripts | Manual/diagnostic only. They are not UI data contracts. |

## New UI Rules

1. UI pages should call router APIs, not import backend names or mirror legacy service naming.
2. Empty intelligence must show a truthful unavailable/pending state, not filler advice.
3. Official account data, qualified KOL data, and legacy cold KOL records must be scoped separately.
4. Every intelligence card must expose evidence, freshness, status, and allowed actions.
5. Sales attribution, short links, and costs should be composed as one workflow around projects.
6. Product analysis should contain product-vs-performance and competitor analysis only; KOL Top lists belong in KOL search.
7. Advanced diagnostics belong behind settings or data quality, not in the default management dashboard.

## Backend Items Still Open

| Item | Status | UI impact |
|---|---|---|
| Live Google/Reddit/RSS/Gemini ingestion | Not run in this architecture cleanup | UI should present gated/pending state |
| LLM business summaries | Gateway exists, budget/approval gated | Do not treat as active intelligence until explicitly enabled |
| Monthly report automation | Not closed | Reports UI can show weekly generation and monthly pending |
| Frontend domain migration | Separate UI track | Backend is ready enough for UI replacement to begin |
