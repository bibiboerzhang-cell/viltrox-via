# V-KPI Domain OS Line Guard Diff

Date: 2026-05-24 PT

## Scope

- Backup root: `/Users/bibiboer/Documents/V-KPI-backups/full-snapshots/vkpi-full-20260524T200059Z/project`
- Current root: `/Users/bibiboer/Documents/V-KPI——marketing`
- Limit: `800` lines
- Roots: `backend/app`, `frontend/src`, `scripts`
- Tests are excluded from this production-source debt view.

## Summary

| Metric | Count |
|---|---:|
| Backup violations | 60 |
| Current violations | 32 |
| Still over limit, same path | 32 |
| Cleared from backup list | 28 |
| New current-only violations | 0 |

## Interpretation

- `Cleared from backup list` means the exact relative path is no longer above 800 lines in the current tree. It may have been split, deleted, renamed, or moved; follow-up review must classify each item before claiming completed refactor credit.
- `New current-only violations` means the path was not above 800 lines in the backup snapshot but is above 800 lines now. These are regression candidates unless they are intentional new legacy inventory discovered by path changes.
- This report measures file-size debt only. It does not count Domain business migration progress; real Domain migration remains 0 until business code lands under `backend/app/domains/*` or `frontend/src/domains/*`.

## Still Over Limit

| Current lines | Backup lines | Path |
|---:|---:|---|
| 7535 | 7535 | `frontend/src/components/vkpi/pages/RepairCenterPage.tsx` |
| 3406 | 3406 | `backend/app/services/vkpi/repair_repository.py` |
| 3341 | 3334 | `frontend/src/components/vkpi/pages/DashboardPremium.tsx` |
| 2866 | 2866 | `frontend/src/services/vkpi.ui-api.ts` |
| 2699 | 2699 | `backend/app/services/via/session_service.py` |
| 2342 | 2342 | `frontend/src/components/vkpi/pages/projects/ProjectDetailView.tsx` |
| 2338 | 2338 | `backend/app/services/vkpi/memory.py` |
| 2234 | 2234 | `backend/app/services/memory/via_learning.py` |
| 2121 | 2121 | `backend/app/api/routers/admin.py` |
| 2045 | 2045 | `backend/app/services/student_identity.py` |
| 1787 | 1787 | `backend/app/db/repositories/via_control.py` |
| 1699 | 1699 | `frontend/src/components/vkpi/panels/KolPoolPanel.tsx` |
| 1698 | 1698 | `frontend/src/components/vkpi/pages/channels/MyKolMatrix.tsx` |
| 1621 | 1602 | `backend/app/services/vkpi/channels.py` |
| 1549 | 1549 | `backend/app/services/vkpi/media_cache.py` |
| 1414 | 1414 | `backend/app/services/via/product_brain.py` |
| 1367 | 1367 | `backend/app/services/vkpi/analytics.py` |
| 1365 | 1365 | `backend/app/services/vkpi/kol_pool.py` |
| 1306 | 1306 | `backend/app/services/vkpi/new_launch_match.py` |
| 1267 | 1267 | `backend/app/services/vkpi/kol_intelligence_card.py` |
| 1252 | 1252 | `backend/app/services/vkpi/kol_product_fit.py` |
| 1243 | 1239 | `frontend/src/components/vkpi/pages/IntelligenceCenterPage.tsx` |
| 1235 | 1235 | `backend/app/db/migrations.py` |
| 1192 | 1192 | `backend/app/services/vkpi/legacy_import_staging.py` |
| 1187 | 1187 | `backend/app/services/ai/analyzers/claude_vision.py` |
| 1165 | 1165 | `backend/app/api/routers/vkpi_kol_links.py` |
| 1093 | 1016 | `backend/app/services/system/staff.py` |
| 1092 | 1092 | `backend/app/services/vkpi/daily_sync.py` |
| 1058 | 1058 | `backend/app/services/intelligence/account_scan_service.py` |
| 1028 | 1028 | `backend/app/services/scraping/ytdlp.py` |
| 1021 | 1021 | `backend/app/api/routers/audit.py` |
| 981 | 981 | `backend/app/services/vkpi/competitor_brain.py` |

## Cleared From Backup Violation List

| Backup lines | Path | Follow-up classification |
|---:|---|---|
| 4377 | `frontend/src/components/vkpi/VkpiDashboard.css` | TODO: split / delete / rename / move |
| 4060 | `frontend/src/components/vkpi/pages/projects/projectBoard.css` | TODO: split / delete / rename / move |
| 3854 | `frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css` | TODO: split / delete / rename / move |
| 3022 | `frontend/src/components/vkpi/pages/DiscoverPage.tsx` | TODO: split / delete / rename / move |
| 2083 | `frontend/src/components/vkpi/pages/discover/discoverDecision.css` | TODO: split / delete / rename / move |
| 1859 | `frontend/src/components/vkpi/pages/channels/channelKols.css` | TODO: split / delete / rename / move |
| 976 | `backend/app/services/jobs/queue.py` | TODO: split / delete / rename / move |
| 968 | `frontend/src/components/vkpi/pages/channels/channelContent.css` | TODO: split / delete / rename / move |
| 958 | `frontend/src/components/vkpi/pages/SettingsPage.tsx` | TODO: split / delete / rename / move |
| 957 | `backend/app/services/via/knowledge_seed.py` | TODO: split / delete / rename / move |
| 947 | `backend/app/db/connection.py` | TODO: split / delete / rename / move |
| 945 | `backend/app/services/vkpi/legacy_entity_resolution.py` | TODO: split / delete / rename / move |
| 941 | `backend/app/services/ai/orchestrator.py` | TODO: split / delete / rename / move |
| 926 | `backend/app/api/routers/system_admin.py` | TODO: split / delete / rename / move |
| 915 | `backend/app/services/vkpi/data_quality_checks.py` | TODO: split / delete / rename / move |
| 909 | `backend/app/services/vkpi/alerts.py` | TODO: split / delete / rename / move |
| 902 | `backend/app/services/runtime_seed.py` | TODO: split / delete / rename / move |
| 880 | `backend/app/services/vkpi/industry_crawlers/reddit_crawler.py` | TODO: split / delete / rename / move |
| 877 | `frontend/src/components/vkpi/pages/channels/ChannelContentList.tsx` | TODO: split / delete / rename / move |
| 868 | `backend/app/services/kol/account_dossier.py` | TODO: split / delete / rename / move |
| 860 | `backend/app/services/vkpi/legacy_kol_commit.py` | TODO: split / delete / rename / move |
| 832 | `backend/app/main.py` | TODO: split / delete / rename / move |
| 832 | `frontend/src/components/vkpi/dashboard/CommandCenter.tsx` | TODO: split / delete / rename / move |
| 824 | `backend/app/api/routers/kol_ops.py` | TODO: split / delete / rename / move |
| 822 | `backend/app/api/routers/intelligence.py` | TODO: split / delete / rename / move |
| 820 | `backend/app/services/vkpi/comment_intelligence.py` | TODO: split / delete / rename / move |
| 813 | `frontend/src/components/vkpi/pages/data-analysis/NaturalSearchPanel.tsx` | TODO: split / delete / rename / move |
| 810 | `backend/app/services/scraping/apify.py` | TODO: split / delete / rename / move |

## New Current-Only Violations

| Current lines | Path | Follow-up action |
|---:|---|---|
| 0 | none | none |
