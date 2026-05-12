# P3.9 Deep Audit + P3.5-P3.8 Patch

Date: 2026-05-13
Backup: `/Users/bibiboer/Documents/V-KPI-backups/vkpi-before-p39-p35p38-audit-20260513-052807.tar.gz`

## Scope

This pass checks whether P3.9 is deeply complete for the current project workflow, then patches low-risk regressions from the earlier P3.5-P3.8 surface without reopening broad Socialinsider-level work.

## Evidence Reviewed

- `P3_9F_BUTTON_QA_20260513.md`: button inventory and risky action list.
- `P3_9G_ENDPOINT_QA_20260513.md`: export, report, refresh, settings, project, attachment endpoint QA.
- `P3_9H_BROWSER_RISKY_BUTTON_QA_20260513.md`: browser QA for risky buttons.
- `P3_9I_PROJECT_BROWSER_FLOW_QA_20260513.md`: browser QA for project create, stage advance, cost entry.
- `P3_9J_WEEKLY_REPORT_UX_QA_20260513.md`: weekly report UX feedback path.
- `P3_9K_PROJECT_ATTACHMENTS_QA_20260513.md`: project attachment frontend and API readback.
- `P3_9L_EXPORT_REPORT_ENDPOINT_QA_20260513.md`: export/report endpoint and download URL checks.
- `P3_9M_P3_PROJECT_FLOW_STATUS_20260513.md`: P3 project-flow closeout status.

## Current Verdict

P3.9 is complete for the current project-flow boundary:

- Project create uses existing KOL selector when KOL options exist.
- Project create supports product single primary selection plus multi-product association.
- Project row opens detail drawer through a real action.
- Detail drawer includes message/content/terms/shipment evidence uploads.
- Stage advance and shipping/promo cost entry have real API paths.
- PDF/CSV export and weekly report generation have real endpoint paths and visible feedback.

This does not mean Socialinsider-level analytics is complete. Sentiment, Topic Tracking, Pillars, full media playback, compare charts, and complete team ownership filtering remain later product rounds.

## Patch Applied

1. KOL Pool promote-to-main no longer falls back to a manual main KOL ID prompt.

Reason: manual ID linking is too error-prone for production workflow. The expected path is import/claim -> automatic create or match -> linked main KOL.

2. Project creation no longer presents manual KOL ID entry as the normal path when no KOL options exist.

Reason: the fallback is only for temporary testing. The UI now tells users to claim/import KOLs first.

3. Static smoke updated to protect the new fallback wording.

## Verification Plan

Focused verification:

```bash
./scripts/run_smoke.sh \
  smoke_vkpi_kol_pool_promote_to_main.py \
  smoke_vkpi_kol_pool_decision_view_frontend.py \
  smoke_vkpi_project_create_selection_flow.py \
  smoke_vkpi_p2_28_project_flow_frontend.py
```

P3.5-P3.9 regression verification:

```bash
./scripts/run_smoke.sh \
  smoke_vkpi_project_create_selection_flow.py \
  smoke_vkpi_p2_28_project_flow_frontend.py \
  smoke_vkpi_project_evidence_detail_flow.py \
  smoke_vkpi_p2_26_project_attachments.py \
  smoke_vkpi_reports_export_appendix.py \
  smoke_vkpi_weekly_reports_service.py \
  smoke_vkpi_weekly_ai_summary.py \
  smoke_vkpi_kol_pool_platform_mapping.py \
  smoke_vkpi_kol_pool_promote_to_main.py \
  smoke_vkpi_kol_pool_decision_view_frontend.py \
  smoke_vkpi_daily_digest_staff_scope.py \
  smoke_vkpi_daily_digest_kol_pool_bridge.py \
  smoke_vkpi_daily_digest_unique_assignment.py \
  smoke_vkpi_phase1_suggestion_claim_bridge.py \
  smoke_vkpi_p3_1h_button_actions.py
```

## Still Open After This Patch

- P3.8/P3.10 collaboration model is not globally complete: full API-wide owner/team filtering remains a separate security/product round.
- System Settings platform crawl UI still needs compact list + right-side platform detail panel.
- Sentiment, Topic Tracking, Pillars, and Compare remain partial compared with Socialinsider.
- Media/video UX still needs full-content mode, original-post open, playback fallback, and single-post analysis.
- Export/report endpoints are real, but browser-level download/open QA should continue whenever the UI changes.

## Verification Results

Completed on 2026-05-13:

- `npm run build`: PASS.
- Focused smoke batch: PASS=4 / FAIL=0 / TOTAL=4.
- P3.5-P3.9 regression batch: PASS=15 / FAIL=0 / TOTAL=15.
