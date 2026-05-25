# V-KPI Cleared Line-Guard Violation Classification

Date: 2026-05-24 PT

## Purpose

This classifies the 28 files that were above the 800-line guard in the backup snapshot but are no longer above the limit in the current tree.

This is evidence for file-size debt reduction only. It is not evidence of Domain business migration. Most extracted code still lands in legacy locations and must be moved into `domains/*` during later Domain PoC and follow-up migrations.

## Summary

| Classification | Count |
|---|---:|
| Split in place into wrapper CSS | 6 |
| Split or trimmed in place | 22 |
| Deleted or moved away | 0 |
| New current-only violations | 0 |

## Classification

| Backup lines | Current lines | Classification | Path | Related current files |
|---:|---:|---|---|---|
| 4377 | 9 | split_in_place_wrapper | `frontend/src/components/vkpi/VkpiDashboard.css` | `frontend/src/components/vkpi/styles/vkpi-alerts-detail.css`, `frontend/src/components/vkpi/styles/vkpi-dashboard-widgets.css`, `frontend/src/components/vkpi/styles/vkpi-intelligence-kol-agents.css`, `frontend/src/components/vkpi/styles/vkpi-repair-execution.css` |
| 4060 | 8 | split_in_place_wrapper | `frontend/src/components/vkpi/pages/projects/projectBoard.css` | `frontend/src/components/vkpi/pages/projects/styles/campaign-execution-finance.css`, `frontend/src/components/vkpi/pages/projects/styles/campaign-materials-contracts.css`, `frontend/src/components/vkpi/pages/projects/styles/campaign-overview.css`, `frontend/src/components/vkpi/pages/projects/styles/project-board-list.css` |
| 3854 | 7 | split_in_place_wrapper | `frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css` | `frontend/src/components/vkpi/pages/data-analysis/styles/parts/data-analysis-base.css`, `frontend/src/components/vkpi/pages/data-analysis/styles/parts/data-analysis-brains.css`, `frontend/src/components/vkpi/pages/data-analysis/styles/parts/data-analysis-natural-search.css` |
| 3022 | 785 | split_or_trimmed_in_place | `frontend/src/components/vkpi/pages/DiscoverPage.tsx` | `frontend/src/components/vkpi/pages/discover/*` |
| 2083 | 6 | split_in_place_wrapper | `frontend/src/components/vkpi/pages/discover/discoverDecision.css` | `frontend/src/components/vkpi/pages/discover/styles/discover-base.css`, `discover-search.css`, `discover-profile.css`, `discover-context.css` |
| 1859 | 5 | split_in_place_wrapper | `frontend/src/components/vkpi/pages/channels/channelKols.css` | `frontend/src/components/vkpi/pages/channels/styles/channel-kols-accounts.css`, `channel-kols-content.css`, `channel-kols-shell.css` |
| 976 | 743 | split_or_trimmed_in_place | `backend/app/services/jobs/queue.py` | `backend/app/services/jobs/queue_common.py`, `backend/app/services/jobs/queue_inprocess.py` |
| 968 | 3 | split_in_place_wrapper | `frontend/src/components/vkpi/pages/channels/channelContent.css` | `frontend/src/components/vkpi/pages/channels/styles/channel-content-cards.css`, `channel-content-overlays.css`, `channel-content-reddit-responsive.css` |
| 958 | 799 | split_or_trimmed_in_place | `frontend/src/components/vkpi/pages/SettingsPage.tsx` | needs immediate follow-up; one line below ceiling |
| 957 | 787 | split_or_trimmed_in_place | `backend/app/services/via/knowledge_seed.py` | `backend/app/services/via/knowledge_seed_dynamic_docs.py` |
| 947 | 733 | split_or_trimmed_in_place | `backend/app/db/connection.py` | `backend/app/db/connection_sql_translation.py` |
| 945 | 753 | split_or_trimmed_in_place | `backend/app/services/vkpi/legacy_entity_resolution.py` | `backend/app/services/vkpi/legacy_entity_resolution_decisions.py`, `legacy_entity_resolution_format.py` |
| 941 | 676 | split_or_trimmed_in_place | `backend/app/services/ai/orchestrator.py` | `backend/app/services/ai/orchestrator_analyzers.py` |
| 926 | 584 | split_or_trimmed_in_place | `backend/app/api/routers/system_admin.py` | `backend/app/api/routers/system_admin_staff.py` |
| 915 | 639 | split_or_trimmed_in_place | `backend/app/services/vkpi/data_quality_checks.py` | no obvious extracted sibling; verify whether behavior was deleted or compacted |
| 909 | 778 | split_or_trimmed_in_place | `backend/app/services/vkpi/alerts.py` | `backend/app/services/vkpi/alerts_common.py`, `alerts_detail.py` |
| 902 | 765 | split_or_trimmed_in_place | `backend/app/services/runtime_seed.py` | `backend/app/services/runtime_seed_data.py` |
| 880 | 614 | split_or_trimmed_in_place | `backend/app/services/vkpi/industry_crawlers/reddit_crawler.py` | no obvious extracted sibling |
| 877 | 535 | split_or_trimmed_in_place | `frontend/src/components/vkpi/pages/channels/ChannelContentList.tsx` | `frontend/src/components/vkpi/pages/channels/ChannelContentList.helpers.ts` |
| 868 | 706 | split_or_trimmed_in_place | `backend/app/services/kol/account_dossier.py` | `backend/app/services/kol/account_dossier_rules.py` |
| 860 | 779 | split_or_trimmed_in_place | `backend/app/services/vkpi/legacy_kol_commit.py` | `backend/app/services/vkpi/legacy_kol_commit_format.py` |
| 832 | 768 | split_or_trimmed_in_place | `backend/app/main.py` | `backend/app/main_health.py` |
| 832 | 604 | split_or_trimmed_in_place | `frontend/src/components/vkpi/dashboard/CommandCenter.tsx` | `frontend/src/components/vkpi/dashboard/CommandCenter.helpers.ts` |
| 824 | 634 | split_or_trimmed_in_place | `backend/app/api/routers/kol_ops.py` | `backend/app/api/routers/kol_ops_import.py` and existing split routers |
| 822 | 776 | split_or_trimmed_in_place | `backend/app/api/routers/intelligence.py` | `backend/app/api/routers/intelligence_system.py` and existing split routers |
| 820 | 637 | split_or_trimmed_in_place | `backend/app/services/vkpi/comment_intelligence.py` | `backend/app/services/vkpi/comment_intelligence_rules.py` |
| 813 | 645 | split_or_trimmed_in_place | `frontend/src/components/vkpi/pages/data-analysis/NaturalSearchPanel.tsx` | `frontend/src/components/vkpi/pages/data-analysis/NaturalSearchPanel.helpers.ts` |
| 810 | 517 | split_or_trimmed_in_place | `backend/app/services/scraping/apify.py` | `backend/app/services/scraping/apify_viltrox_comments.py` |

## Risk Notes

- `SettingsPage.tsx` is at 799 lines. It technically passes the guard but should be treated as still at risk.
- Several files remain close to the ceiling: `knowledge_seed.py`, `legacy_kol_commit.py`, `alerts.py`, `main.py`, and `intelligence.py`.
- Most cleared files are still in legacy directories. They reduce file-size risk but do not count as Domain migration.
- CSS wrapper files are acceptable as temporary compatibility entrypoints, but the underlying part files should move with their owning domains during migration.

## Next Action

Use this classification when deciding what can be committed as file-size debt reduction, and keep it separate from the first real Domain PoC.
