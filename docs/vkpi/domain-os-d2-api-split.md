# Domain OS D2 API Split

Status: D2-A through D2-G complete

## Scope

This batch creates domain API facades without deleting the legacy `frontend/src/services/vkpi.ui-api.ts` bus.

New files:

- `frontend/src/services/vkpi/attribution-api.ts`
- `frontend/src/services/vkpi/audit-api.ts`
- `frontend/src/services/vkpi/channel-api.ts`
- `frontend/src/services/vkpi/cost-api.ts`
- `frontend/src/services/vkpi/dashboard-api.ts`
- `frontend/src/services/vkpi/data-quality-api.ts`
- `frontend/src/services/vkpi/evidence-api.ts`
- `frontend/src/services/vkpi/feedback-api.ts`
- `frontend/src/services/vkpi/industry-api.ts`
- `frontend/src/services/vkpi/intelligence-api.ts`
- `frontend/src/services/vkpi/kol-api.ts`
- `frontend/src/services/vkpi/links-api.ts`
- `frontend/src/services/vkpi/market-api.ts`
- `frontend/src/services/vkpi/media-api.ts`
- `frontend/src/services/vkpi/projects-api.ts`
- `frontend/src/services/vkpi/product-api.ts`
- `frontend/src/services/vkpi/repair-api.ts`
- `frontend/src/services/vkpi/search-api.ts`
- `frontend/src/services/vkpi/settings-api.ts`
- `frontend/src/services/vkpi/staff-api.ts`
- `frontend/src/services/vkpi/tasks-api.ts`

Updated:

- `frontend/src/services/vkpi/index.ts`
- `frontend/src/components/vkpi/pages/IntelligenceCenterPage.tsx`
- `frontend/src/components/vkpi/pages/DashboardPremium.tsx`
- `frontend/src/components/vkpi/dashboard/CommandCenter.tsx`
- `frontend/src/components/vkpi/pages/AgentsPage.tsx`
- `frontend/src/components/vkpi/pages/channels/useOfficialChannelMatrix.ts`
- `frontend/src/components/vkpi/pages/data-analysis/CompetitorBrainPanel.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/ContentBrainPanel.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/MarketIntelligencePanel.tsx`
- `frontend/src/components/vkpi/pages/AttributionPage.tsx`
- `frontend/src/components/vkpi/pages/AuditPage.tsx`
- `frontend/src/components/vkpi/pages/CostsPage.tsx`
- `frontend/src/components/vkpi/pages/costs/BudgetMonitorPanel.tsx`
- `frontend/src/components/vkpi/pages/DataQualityPage.tsx`
- `frontend/src/components/vkpi/pages/LinksPage.tsx`
- `frontend/src/components/vkpi/pages/ChannelsPage.tsx`
- `frontend/src/components/vkpi/pages/channels/ChannelContentList.tsx`
- `frontend/src/components/vkpi/pages/channels/MyKolMatrix.tsx`
- `frontend/src/components/vkpi/pages/channels/RedditAssessmentPanel.tsx`
- `frontend/src/components/vkpi/pages/channels/useOfficialChannelGaps.ts`
- `frontend/src/components/vkpi/hooks/useProjectDetail.ts`
- `frontend/src/components/vkpi/hooks/useProjectDetailDrawer.ts`
- `frontend/src/components/vkpi/pages/CampaignsPage.tsx`
- `frontend/src/components/tasks/TaskCenter.tsx`
- `frontend/src/components/vkpi/shared/mediaProxy.ts`
- `frontend/src/components/vkpi/shared/FeedbackWidget.tsx`
- `frontend/src/components/vkpi/pages/settings/SettingsFeedbackPanel.tsx`
- `frontend/src/components/vkpi/pages/AnalyticsPage.tsx`
- `frontend/src/components/vkpi/pages/ProductBattlePage.tsx`
- `frontend/src/components/vkpi/pages/analytics/AnalyticsMonitorPanel.tsx`
- `frontend/src/components/vkpi/pages/analytics/RecommendationRunReviewPanel.tsx`
- `frontend/src/components/vkpi/pages/analytics/useProductRecommendationActions.ts`
- `frontend/src/components/vkpi/pages/analytics/useRecommendationEvidence.ts`
- `frontend/src/components/vkpi/intelligence/intelligenceFeedback.ts`
- `frontend/src/components/vkpi/pages/SettingsPage.tsx`
- `frontend/src/components/vkpi/pages/settings/SettingsAdminCards.tsx`
- `frontend/src/components/vkpi/pages/settings/StaffPermissionDrawer.tsx`
- `frontend/src/components/vkpi/pages/settings/SettingsOperatingReviewPanel.tsx`
- `frontend/src/components/vkpi/pages/settings/staffPermissionTemplates.ts`
- `frontend/src/routes/admin/StaffActivateRoute.tsx`
- `frontend/src/components/admin/tabs_v2/VkpiTab.tsx`
- `frontend/src/components/vkpi/VkpiDashboard.tsx`
- `frontend/src/components/vkpi/pages/DiscoverPage.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/NaturalSearchPanel.tsx`
- `frontend/src/components/vkpi/pages/data-analysis/shared/CommentIntelligencePanel.tsx`

## What Moved

`IntelligenceCenterPage.tsx` now imports these calls from domain API facades:

- `getDashboardAgentsInbox` from `dashboard-api`
- `getRecommendationFeedbackBacklog` from `intelligence-api`
- `getCompetitorBrainReviewSuggestions` from `market-api`
- `getMarketExternalDailyPlanV0` from `market-api`
- `getMarketIntelligenceCardsV0` from `market-api`
- `getMarketIntelligenceV0` from `market-api`
- `listCompetitorBrainSignals` from `market-api`

Additional low-risk callers moved in this batch:

- Dashboard shell calls moved to `dashboard-api`, `intelligence-api`, `kolPool-api`, and `market-api`.
- Agents inbox moved to `dashboard-api`.
- Official channel matrix hook moved to `dashboard-api`.
- Market/competitor/content brain panels moved to `market-api`.
- Attribution page moved to `attribution-api`.
- Cost detail and AI budget monitor moved to `cost-api`.
- Audit overview moved to `audit-api`.
- Data quality actions moved to `data-quality-api`; brand signal review moved to `market-api`.
- Short link detail moved to `links-api`.
- Channel matrix/posts/comments/gaps/Reddit assessment/video-cache enqueue moved to `channel-api`.
- KOL channel profile/posts/comments/claim/scan/update calls moved to `kol-api`.
- Project detail, campaign, budget pool, and offboarding calls moved to `projects-api`.
- Task polling/SSE/cancel/retry calls moved to `tasks-api`.
- Cached video lookup moved to `media-api`.
- Team feedback submit/list/update moved to `feedback-api`.
- Product analytics, launches, recommendations, outcomes, evidence, and outreach digest moved to `product-api`.
- Staff invite/activation/password reset/RBAC permission types moved to `staff-api`.
- Runtime settings, provider probes, feature flags, crawl settings, budget settings, and comment alert settings moved to `settings-api`.
- Industry project/account/cross-platform APIs moved to `industry-api`.
- Natural search and KOL decision audit moved to `search-api`.
- Comment intelligence overview/process/retry moved to `comment-intelligence-api`.
- Top-level dashboard alert/KOL/staff profile calls moved to `alert-api`, `kol-api`, and `staff-api`.
- Discover page KOL, KOL Pool, product recommendation, market signal, and search calls moved to their domain facades.
- Admin `VkpiTab` action calls moved to domain facades; only the legacy dashboard data aggregator remains wrapped by `dashboard-api`.

## Compatibility

The legacy `vkpi.ui-api.ts` remains in place for old callers. The new domain files are facades over the same HTTP routes.

`dashboard-api.fetchVkpiDashboardData` is a temporary legacy adapter over the old dashboard data builder. It keeps page components off the monolithic API bus while the larger dashboard data builder is split in a later batch.

The `repair-api` facade is intentionally not wired into `RepairCenterPage.tsx` yet. Repair Center has a large type surface and must be migrated with its freeze/split batch, not as a generic `Record<string, unknown>` replacement.

## Validation

Commands:

```bash
npm --prefix frontend run build
PYTHONPATH=backend .venv/bin/python scripts/check_line_guard.py --no-tests
```

Result:

- Frontend build passed.
- New API facade files are below 800 lines.
- Existing source debt remains tracked by the line guard.
- Direct references to the legacy `vkpi.ui-api.ts` bus are now down to 5 current matches: 1 temporary dashboard data adapter, 1 Repair Center import, 2 Repair evidence path strings, and 1 lineage explanatory comment. Product-facing dashboard/search/Admin components no longer import the monolithic API bus directly.

## Next API Split Targets

1. Split `dashboard-api.fetchVkpiDashboardData` into smaller dashboard/project/link/cost/report builders.
2. Split `DiscoverPage` into subcomponents now that its API surface is on domain facades.
3. Move Repair imports only after Repair Center v0 freeze/split starts.
4. Delete or shrink `vkpi.ui-api.ts` after all callers leave it.
5. Stop adding new exports to `vkpi.ui-api.ts`.
