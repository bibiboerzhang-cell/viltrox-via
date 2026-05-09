# V-KPI Architecture Backbone

This project is now treated as a separate internal marketing management system,
not as a continuation of VIA creator-facing product work.

## Objective

Build one auditable operating path for marketing work:

```text
staff -> KOL -> project -> short link -> click -> Shopify/Amazon sales -> cost -> KPI
```

The reused V-OS code remains useful as infrastructure, but V-KPI owns the new
business flow.

## Core Layers

1. Identity and permission layer

Reuses `users`, `staff`, admin auth, tab permissions, and audit patterns.

2. Workflow layer

New tables:

```text
vkpi_projects
vkpi_project_stage_events
vkpi_kol_claims
```

This layer answers who owns a KOL, where a project is stuck, and whether a claim
should remain active.

3. Link center

New tables:

```text
vkpi_links
vkpi_link_destinations
vkpi_link_clicks
```

This becomes the replacement backbone for Bitly/Geniuslink. Every external
marketing link should be created as a V-KPI link first, then routed to Shopify,
Amazon Attribution, Amazon Associates, Geniuslink, Bitly, or a normal landing
page if needed.

4. Attribution and ROI layer

New tables:

```text
vkpi_sales_attributions
vkpi_cost_ledger
vkpi_kpi_ledger
```

This layer separates confirmed Shopify orders, Amazon Attribution imports,
manual imports, costs, and final KPI credit.

5. Decision layer

New tables:

```text
vkpi_alerts
vkpi_decision_snapshots
```

This layer powers management questions such as stalled KOLs, links with weak
conversion, unassigned sales, overdue projects, and API cost overruns.

## Reused V-OS Layers

```text
kols
kol_candidates
kol_account_snapshots
orders
platform_ingest_events
ai_usage_log
provider_status
job queue / worker
```

## New API Surface

Admin:

```text
GET  /api/admin/vkpi/architecture
GET  /api/admin/vkpi/dashboard
GET  /api/admin/vkpi/workflow/stages
GET  /api/admin/vkpi/projects
POST /api/admin/vkpi/projects
POST /api/admin/vkpi/projects/{project_id}/stage
GET  /api/admin/vkpi/links
POST /api/admin/vkpi/links
GET  /api/admin/vkpi/alerts
GET  /api/admin/vkpi/kpi-ledger
```

Public redirect:

```text
GET /go/{slug}
```

The production short-link domain can route `go.viltrox.com/{slug}` to
`/go/{slug}` at the edge.

## First Acceptance Checks

1. V-KPI router imports with the existing app.
2. Local SQLite can create V-KPI tables on first API use.
3. Postgres migration sequence includes `023_vkpi_core.sql`.
4. Admin can create a V-KPI project and move it through a stage event.
5. Admin can create a V-KPI link.
6. Public `/go/{slug}` records a click and redirects with status `302`.
