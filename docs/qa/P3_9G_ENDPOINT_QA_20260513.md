# P3.9G Endpoint-Level QA - Risky Button Coverage

Date: 2026-05-13
Workspace: /Users/bibiboer/Documents/V-KPI——marketing
Backup: /Users/bibiboer/Documents/V-KPI-backups/vkpi-before-p39g-endpoint-qa-20260513-015203.tar.gz

## Scope

P3.9G checks whether previously skipped high-risk UI buttons have real backend endpoints and smoke coverage. This round does not click destructive or externally visible browser actions directly; it verifies the endpoint wiring and bounded smoke paths first.

## Endpoint Mapping

| UI action | Frontend caller | Backend endpoint | Current verdict |
|---|---|---|---|
| Export PDF | `exportVkpiReport("pdf")` in `frontend/src/services/vkpi.ui-api.ts` | `POST /api/marketing/exports/pdf` | Endpoint exists; export smoke passed. Browser download UX still needs controlled click QA. |
| Export CSV | `exportVkpiReport("csv")` | `POST /api/marketing/exports/csv` | Endpoint exists; export smoke passed. Browser download UX still needs controlled click QA. |
| Generate weekly report | `generateWeeklyReport()` | `POST /api/marketing/reports/weekly/generate` | Endpoint exists; weekly service/offline/AI-summary smokes passed. Browser click should be tested in a controlled run. |
| Refresh account | `refreshIndustryAccount(accountId)` | `POST /api/admin/vkpi/industry-data/accounts/{id}/refresh` | Endpoint exists; single-account refresh smoke passed. |
| Settings platform save | `updatePlatformCrawlSettings(...)` | `PATCH /api/admin/vkpi/settings/platform-crawl` | Endpoint exists; settings crawl UI smoke passed. UI is still too dense and remains P3.9/P3.10 UX debt. |
| Move project stage | `transitionProjectStage(...)` | `POST /api/marketing/projects/{project_id}/stage` | Endpoint exists; project create/selection/flow smokes passed. |
| Add project cost | `addProjectCost(...)` | `POST /api/marketing/projects/{project_id}/costs` | Endpoint exists; project flow smoke passed. |
| Attachments / evidence | Project drawer handlers | Project evidence/attachment endpoints | Project attachment + evidence-detail smokes passed. |

## Smoke Evidence

All commands were run through `./scripts/run_smoke.sh`, not naked Python, so the runtime environment and database selection use the project standard wrapper.

| Smoke | Result |
|---|---|
| `smoke_vkpi_reports_export_appendix.py` | PASS |
| `smoke_vkpi_weekly_reports_service.py` | PASS |
| `smoke_vkpi_weekly_reports_offline.py` | PASS |
| `smoke_vkpi_weekly_ai_summary.py` | PASS |
| `smoke_vkpi_p2_27_single_account_refresh.py` | PASS |
| `smoke_vkpi_p3_1d_settings_crawl_ui.py` | PASS |
| `smoke_vkpi_project_create_selection_flow.py` | PASS |
| `smoke_vkpi_p2_28_project_flow_frontend.py` | PASS |
| `smoke_vkpi_p2_26_project_attachments.py` | PASS |
| `smoke_vkpi_project_evidence_detail_flow.py` | PASS |

Summary: 10/10 targeted endpoint smokes passed.

## Findings

1. The risky buttons are no longer pure fake buttons at the service/API level; export, weekly report, refresh, platform settings, project stage, cost, and attachment paths all have real backend routes with smoke coverage.
2. Browser-level validation is still incomplete for download/open flows because the current smoke layer verifies backend behavior, not whether the browser opens the returned file cleanly.
3. The Settings platform crawl UI remains functionally wired but visually overloaded; endpoint QA passing does not mean the operator UX is acceptable.
4. `client_matches_server=false` on `/health` is still a separate version-consistency issue and should not be mixed into endpoint correctness, but it must be fixed before team handoff.

## Next Round Candidate

P3.9H should be a controlled browser QA pass for exactly these risky actions:

- Click `导出 PDF` and confirm a real `downloadUrl` opens or downloads.
- Click `导出 CSV` and confirm a real `downloadUrl` opens or downloads.
- Click `生成周报` once in a bounded test context and confirm report generation state and download/open behavior.
- Click `刷新账号` on a known seeded/synced account and confirm status transition without 500.
- Click one project stage transition and one cost registration against a test project only.
- Record every button as `real`, `disabled-with-reason`, or `still fake`.

## Acceptance Status

P3.9G endpoint QA: PASS for smoke-backed endpoint coverage.

Still pending before calling the page production-ready:

- Browser download/open QA for export and weekly report.
- Settings platform crawl UI compaction.
- Full visual button audit after UI compaction.
- `client_matches_server=false` version/hash consistency fix.
