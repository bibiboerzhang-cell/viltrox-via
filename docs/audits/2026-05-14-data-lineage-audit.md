# V-KPI Data Lineage Audit

Date: 2026-05-14
Scope: frontend KPI cards, charts, tables, account drawers, data-analysis pages, and backend metric lineage/data source services.
Mode: audit only. No feature code changes in this round.

## Executive Summary

V-KPI has two different lineage layers today:

1. Central metric lineage is real and production-shaped for dashboard metrics. It computes metric snapshots, persists source rows, exposes drilldown endpoints, and the frontend has an evidence drawer.
2. Social/industry analytics lineage is real-data based but not centrally traceable. Account KPI cards, KOL Pool metrics, post cards, and outreach rows mostly read real API/DB payloads, but they do not expose a unified source/drilldown explanation per KPI.

This means P4 should not add broad new analytics features first. The next useful work is to make existing real numbers explainable: source tooltip, drilldown/open-source links, freshness timestamps, and consistent beta labeling for local-only controls.

## Central Metric Lineage Coverage

### Covered Metrics

The following metric keys are defined in `backend/app/services/vkpi/metric_definitions.py` and computed in `backend/app/services/vkpi/metric_lineage_compute.py`:

| Metric | Source | Drilldown State | Notes |
|---|---|---:|---|
| `gmv` | `vkpi_sales_attributions` + Shopify order snapshot join | Yes | Persists source rows and calculation metadata. |
| `cost` | `vkpi_cost_ledger` | Yes | Excludes `status='void'`; cost page also exposes evidence. |
| `net_contribution` | derived from `gmv - cost` | Yes | Derived source relationship is persisted. |
| `roi` | derived from `gmv / cost` | Yes | Derived source relationship is persisted. |
| `new_kol` | `vkpi_kol_claims` | Yes | Source rows include staff/project/KOL references. |
| `published_content` | `vkpi_project_stage_events` | Yes | Uses stage events for published/posted content. |
| `valid_clicks` | `vkpi_link_clicks` | Yes | Bot filtering in formula. |
| `views` | `vkpi_content_posts` | Yes | Content-post source type. |
| `active_projects` | `vkpi_projects` | Yes | Current active state metric. |
| `alerts` | `vkpi_alerts` | Yes | Open-alert source type. |

### Frontend Evidence Layer

| UI Area | File | State |
|---|---|---|
| Main V-KPI dashboard cards | `frontend/src/components/vkpi/VkpiDashboard.tsx` | Uses `useMetricEvidence`, `openMetricEvidence`, and evidence drawer. |
| Evidence drawer | `frontend/src/components/vkpi/drawers/EvidenceDrawer.tsx` | Shows run uid, period, definition version, source rows, project/KOL/staff refs. |
| Cost page | `frontend/src/components/vkpi/pages/CostsPage.tsx` | Cost drilldown path exists for the cost metric. |

Verdict: central dashboard metrics are lineage-ready. This layer is not the main gap.

## Data Analysis / Account Analytics Coverage

### Backend Source Reality

Data Analysis account KPIs come from real platform/account data through these paths:

| Layer | File | Role |
|---|---|---|
| Snapshot KPI normalization | `backend/app/services/vkpi/industry_snapshot_kpis.py` | Calculates followers, posts, views, engagement, posting signals, organic value-related fields. |
| Snapshot collection | `backend/app/services/vkpi/industry_snapshot_collector.py` | Collects raw platform data and writes account/post snapshot rows. |
| Post rows | `vkpi_industry_posts` | Stores media/post metrics such as views, likes, comments, shares, saves, thumbnails/media fields. |
| Account/snapshot rows | `vkpi_industry_accounts`, `vkpi_industry_snapshots` | Stores profile and normalized snapshot state. |

The KPI normalization already protects real zero values via `_first_present`/known-value helpers. Unknown fields intentionally remain null rather than being converted into fake zeroes.

### Frontend Source Reality

| UI Area | File | State |
|---|---|---|
| Account profile dashboard | `frontend/src/components/vkpi/pages/data-analysis/profile/ProfileDashboard.tsx` | Followers/views/engagement/posts read latest snapshot/account/posts. Real but no central lineage drawer. |
| Account drawer summary/content tabs | `frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx` | Reads snapshots/posts and computes totals locally. Real but local lineage only. |
| Cross-platform panel metrics | `frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx` + `utils/metricHelpers.ts` | Uses account summaries + visible posts. Real but front-end aggregation is not persisted as a lineage run. |
| Posts tab | `frontend/src/components/vkpi/pages/data-analysis/tabs/PostsTab.tsx` | Shows real post rows and can open post detail. |
| Post detail drawer | `frontend/src/components/vkpi/pages/data-analysis/drawers/PostDetailDrawer.tsx` | Shows post metrics and analysis fields; not a formal metric-lineage source view. |

Verdict: Data Analysis numbers are not fake, but their explanation is weaker than central dashboard metrics. They need source/freshness UI and selected drilldowns, not a full backend rewrite in this step.

## KOL Pool / Candidate Table Lineage

| Area | Source | State | Gap |
|---|---|---|---|
| Candidate rows | `vkpi_kol_pool` and import/enrich logic | Real imported/enriched records. | Per-row source provenance is visible only as basic source label, not as a full audit trail. |
| Metrics like followers/avg views/engagement | Import payload + platform enrichment + snapshots | Real if present; fixed zero-value mapping in `kol_pool.py`. | No cell-level source explanation. |
| Promote/link action | Existing routers/services | Real action path. | Needs clear provenance after promotion: imported from file vs platform refresh vs manual correction. |

Verdict: KOL Pool lineage should be solved by a row source popover and enrichment history, not by central metric lineage immediately.

## Outreach / Daily Top100 Lineage

| Area | Source | State | Gap |
|---|---|---|---|
| Daily digest assignment | digest/suggestion services | Real assignment logic exists. | Candidate-source visibility is still weak: why this candidate appeared, from which product/platform/run. |
| Outreach suggestions | `vkpi_outreach_suggestions` | Real table flow, but upstream source can be empty if monitor/product jobs do not run. | Need source-run diagnostics and `why assigned` explanation. |
| UI rows | outreach tables | Shows profile/original links where available. | Not tied to central lineage/evidence drawer. |

Verdict: Top100 should receive source-run diagnostics and explainability first. Do not treat an empty candidate list as fake if no upstream distributable candidates exist.

## Chart / Control Lineage

| Control/Chart | State | Recommendation |
|---|---|---|
| Metric picker / selected charts | Local view control. | Label as local/beta unless backed by persisted backend aggregation. |
| Filters | Local filtering over loaded accounts/posts. | Acceptable for P3/P4 internal use; add visible scope/freshness text. |
| Trend/content/leaderboard charts | Render passed data. | Add source tooltip showing account/posts/snapshot basis. |
| Compare view | Frontend comparison over loaded accounts/posts. | Keep as beta unless dedicated backend compare run is implemented. |
| Pillars/Sentiment/Topic Tracking | Partial/local or downstream-dependent. | Must show `beta`/`requires synced content` state until backend run lineage exists. |

Verdict: these controls should not be called fake if they clearly say local/beta. They become risky only if the UI implies server-grade Socialinsider parity.

## Risk Classification

| Finding | Severity | Reason |
|---|---:|---|
| Central metric lineage exists and is wired to dashboard evidence drawer. | Good | Dashboard-level core metrics are traceable. |
| Data Analysis KPI cards lack source tooltip/drilldown. | P1 | Numbers are real but user cannot see table/freshness/source at point of use. |
| KOL Pool row/cell provenance is incomplete. | P1 | Imported/enriched candidate metrics can be hard to trust when selecting KOLs. |
| Outreach/Top100 candidate source is not clearly explained. | P1 | Users need to know why a candidate was assigned. |
| Local chart/filter controls can be mistaken for backend-grade analytics. | P2 | Mostly a labeling/UX truthfulness issue, not data fabrication. |
| Full Socialinsider-level persisted aggregation is missing. | P5 | Product ambition, not P3/P4 blocker for team-usable V-KPI. |

## Recommended Next Actions

### P4 Lineage Patch A: SourceTooltip for Data Analysis KPI cards

Files likely touched:

- `frontend/src/components/vkpi/pages/data-analysis/profile/ProfileDashboard.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/utils/metricHelpers.ts`
- optional shared component under `frontend/src/components/vkpi/pages/data-analysis/components/`

Acceptance:

- Followers/views/engagement/posts cards show source text: account field, latest snapshot, or post aggregation.
- Shows `last_successful_at`/snapshot date where available.
- No backend schema change.
- `npm run build` passes.

### P4 Lineage Patch B: KOL Pool source popover

Files likely touched:

- `frontend/src/components/vkpi/panels/KolPoolPanel.tsx`
- `backend/app/services/vkpi/kol_pool.py` only if missing source fields are needed.

Acceptance:

- Each row explains source: xlsx import, platform enrichment, manual correction, linked main KOL.
- Metrics do not display as equal trust if source is import-only.
- No duplicate promotion behavior changes.

### P4 Lineage Patch C: Top100 source-run diagnostics

Files likely touched:

- digest/suggestion services and the Daily Top100 panel.

Acceptance:

- Empty state says whether there are no candidates, no upstream run, all candidates already assigned, or scope hides them.
- Candidate rows include `why assigned`, product/platform/run/source link.

### P4 Lineage Patch D: Beta labeling for local analytics controls

Files likely touched:

- Data Analysis filter/metric picker/compare/pillars/topic tabs.

Acceptance:

- Local-only controls are explicitly labeled `本地视图 / beta`.
- Users are not led to believe all charts are persisted backend analysis.

## Explicit Non-Goals For This Round

- Do not build full Socialinsider-grade backend aggregation now.
- Do not add new platform crawling logic in this audit round.
- Do not rewrite existing central `metric_lineage` because it is already functional for dashboard metrics.
- Do not mark local controls as fake if the UI truthfully labels them as local/beta.

## Verification Performed

Static inspection covered:

- `frontend/src/components/vkpi/VkpiDashboard.tsx`
- `frontend/src/components/vkpi/drawers/EvidenceDrawer.tsx`
- `frontend/src/components/vkpi/shared/MetricCard.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/**`
- `frontend/src/services/vkpi.lineage-api.ts`
- `backend/app/services/vkpi/metric_definitions.py`
- `backend/app/services/vkpi/metric_lineage.py`
- `backend/app/services/vkpi/metric_lineage_compute.py`
- `backend/app/services/vkpi/industry_snapshot_kpis.py`
- `backend/app/services/vkpi/industry_snapshot_collector.py`
- `backend/app/api/routers/vkpi_attribution_metrics.py`
- `backend/app/api/routers/vkpi_dashboard_staff.py`

