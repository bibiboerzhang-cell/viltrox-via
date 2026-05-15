# P4.2B-1 First-Tier Mutation Safety Matrix

- Generated: 2026-05-15
- Scope: first-tier V-KPI write routers only: `vkpi_settings.py`, `vkpi_industry_automation.py`, `vkpi_evidence_assets.py`, `vkpi_operations.py`.
- Input: P4.2A write endpoint inventory (`296` total write endpoints, `146` V-KPI subset).
- Backup before audit: `/Users/bibiboer/Documents/V-KPI-backups/before-p4-2b-first-tier-mutation-audit-20260515-120635.tar.gz`
- This is an audit/report artifact only. No business code was changed.
- Risk here is launch-prioritization, not proof of an exploitable bug. Service-layer audit was checked where practical; browser confirmation state still needs targeted QA.

## Summary

- First-tier endpoints reviewed: `49`
- P0 launch-before QA/fix candidates: `8`
- P1 should-fix / sample-QA candidates: `29`
- P2 acceptable-for-internal-observation candidates: `12`

### By Router

| Router | Endpoints | P0 | P1 | P2 |
|---|---:|---:|---:|---:|
| `vkpi_settings.py` | 7 | 2 | 3 | 2 |
| `vkpi_industry_automation.py` | 13 | 1 | 9 | 3 |
| `vkpi_evidence_assets.py` | 11 | 0 | 5 | 6 |
| `vkpi_operations.py` | 18 | 5 | 12 | 1 |

## P0 Candidates

| Router | Endpoint | Why It Is P0 | Required Next Action |
|---|---|---|---|
| `vkpi_settings.py` | `PATCH /settings/platform-crawl` | Controls external crawl, budget gates, account/content limits. Audit exists, but blast radius/cost makes this launch-before QA. | P4.2C real QA + confirm/audit/rollback decision |
| `vkpi_settings.py` | `PATCH /settings/budgets` | Global/monthly budget control. Audit exists; needs explicit confirm and visible before/after. | P4.2C real QA + confirm/audit/rollback decision |
| `vkpi_industry_automation.py` | `POST /automation/models/{model_version}/activate` | Global model switch with broad scoring impact and no explicit audit/rollback record. | P4.2C real QA + confirm/audit/rollback decision |
| `vkpi_operations.py` | `POST /budget-pools` | Financial budget object creation with no business audit found. | P4.2C real QA + confirm/audit/rollback decision |
| `vkpi_operations.py` | `POST /budget-pools/{pool_id}/allocate` | Financial allocation. Needs audit, confirmation, and reversal path. | P4.2C real QA + confirm/audit/rollback decision |
| `vkpi_operations.py` | `POST /staff/{staff_id}/offboard/initiate` | Prepares staff offboarding; low direct mutation but sensitive HR/business action. | P4.2C real QA + confirm/audit/rollback decision |
| `vkpi_operations.py` | `POST /offboarding/{run_id}/execute` | Highest-risk multi-table mutation. Result log exists but no explicit audit/rollback transaction plan. | P4.2C real QA + confirm/audit/rollback decision |
| `vkpi_operations.py` | `POST /cron/{job_name}/run` | Admin endpoint can trigger broad jobs with provider calls and bulk writes. | P4.2C real QA + confirm/audit/rollback decision |

## Real Failure Signal

- `vkpi_industry_accounts.id=743` (`instagram:godox_global`) currently has `crawl_enabled=1`, `sync_status=queued`, `crawl_error_count=8`, `last_crawled_at=2026-05-15T03:10:15Z`, `last_successful_at=2026-05-11T06:25:13Z`.
- The account table has a failure counter but no dedicated `error_type` column. Raw platform data shows prior successful Instagram crawler payload, so the current problem is operational/error attribution rather than missing baseline data.
- P4.2C should include `POST /industry-data/accounts/{account_id}/refresh` for this account or a safe equivalent to verify user-visible behavior and error traceability.

## P4.2C Recommended QA Sample

Run real QA on these first, not a fixed old three-path list:

- `POST /settings/providers/{provider}/probe` in `vkpi_settings.py` — Can hit provider/API while exposed under read+manager. Keep but label as test/probe and include provider result in UI.
- `PATCH /settings/platform-crawl` in `vkpi_settings.py` — Controls external crawl, budget gates, account/content limits. Audit exists, but blast radius/cost makes this launch-before QA.
- `PATCH /settings/budgets` in `vkpi_settings.py` — Global/monthly budget control. Audit exists; needs explicit confirm and visible before/after.
- `POST /industry-data/projects/{project_id}/accounts/import` in `vkpi_industry_automation.py` — Bulk write path. Needs count-level audit before broad team use.
- `POST /industry-data/projects/{project_id}/apify/import` in `vkpi_industry_automation.py` — Bulk historical import writes accounts/snapshots/posts. Audit exists but rollback is not one-click.
- `PATCH /industry-data/accounts/{account_id}` in `vkpi_industry_automation.py` — Can set crawl_enabled and queue status. This is the UI close/open crawl path.
- `POST /industry-data/accounts/{account_id}/refresh` in `vkpi_industry_automation.py` — External API path. Real sample account 743 has crawl_error_count=8, last_successful_at=2026-05-11T06:25:13Z.
- `POST /automation/models/{model_version}/activate` in `vkpi_industry_automation.py` — Global model switch with broad scoring impact and no explicit audit/rollback record.
- `POST /evidence/uploads` in `vkpi_evidence_assets.py` — File side effect under runtime/uploads, 25MB allow-list. Needs attachment linkage or upload audit for traceability.
- `POST /shipments/{shipment_id}/receive` in `vkpi_evidence_assets.py` — State transition should have confirm or undo because it changes fulfillment state.
- `POST /analytics/monitor` in `vkpi_operations.py` — Creates suggestions from provider output. This is Daily Top100 candidate-source root.
- `POST /analytics/daily-digest/generate` in `vkpi_operations.py` — Bulk staff assignment write. P3/P4 Daily Top100 correctness depends on this path.
- `POST /analytics/suggestions/{suggestion_id}/create-project` in `vkpi_operations.py` — Multi-step operation: claim, project, optional Shopify short link.
- `POST /budget-pools` in `vkpi_operations.py` — Financial budget object creation with no business audit found.
- `POST /budget-pools/{pool_id}/allocate` in `vkpi_operations.py` — Financial allocation. Needs audit, confirmation, and reversal path.
- `POST /staff/{staff_id}/offboard/initiate` in `vkpi_operations.py` — Prepares staff offboarding; low direct mutation but sensitive HR/business action.
- `POST /offboarding/{run_id}/execute` in `vkpi_operations.py` — Highest-risk multi-table mutation. Result log exists but no explicit audit/rollback transaction plan.
- `POST /cron/{job_name}/run` in `vkpi_operations.py` — Admin endpoint can trigger broad jobs with provider calls and bulk writes.

## Full Matrix

| Risk | Router | Endpoint | Permission | Service Evidence | Confirm | Rollback | P4.2C | Notes |
|---|---|---|---|---|---|---|---|---|
| P1 | `vkpi_settings.py` | `POST /settings/providers/{provider}/probe` | read + manager | No durable audit found; likely external/API probe only | Recommended for paid providers | N/A | Yes | Can hit provider/API while exposed under read+manager. Keep but label as test/probe and include provider result in UI. |
| P1 | `vkpi_settings.py` | `PATCH /settings/feature-flags` | admin | Service writes vkpi_settings_change_logs via audit.log_settings_change | Recommended for non-display flags | Manual revert by writing old value | Sample | Global behavior control. Audit exists; missing confirmed UI confirmation/rollback UX. |
| P0 | `vkpi_settings.py` | `PATCH /settings/platform-crawl` | admin | Service writes vkpi_settings_change_logs via audit.log_settings_change | Required when enabling crawl or raising limits | Manual revert by writing old value | Yes | Controls external crawl, budget gates, account/content limits. Audit exists, but blast radius/cost makes this launch-before QA. |
| P0 | `vkpi_settings.py` | `PATCH /settings/budgets` | admin | Service writes vkpi_settings_change_logs via audit.log_settings_change | Required | Manual revert by writing old value | Yes | Global/monthly budget control. Audit exists; needs explicit confirm and visible before/after. |
| P1 | `vkpi_settings.py` | `PATCH /settings/comment-alerts` | admin + manager | Service writes vkpi_settings_change_logs when actor exists | Recommended | Manual revert | Sample | Notification/alert behavior changes; lower cost risk than crawl/budget. |
| P2 | `vkpi_settings.py` | `PATCH /settings/preferences` | write | Service writes settings change + business event | Not required | Manual revert | No | Per-staff low-risk preference write with scope guard. |
| P2 | `vkpi_settings.py` | `PATCH /settings/notifications` | write | Service writes settings change + business event | Not required | Manual revert | No | Per-staff notification config; service currently marks delivery disabled in audit metadata. |
| P2 | `vkpi_industry_automation.py` | `POST /industry-data/projects` | write | Service writes business event industry_project_create | Optional | Archive via DELETE project | No | Project create path has audit and bounded side effect. |
| P1 | `vkpi_industry_automation.py` | `DELETE /industry-data/projects/{project_id}` | write + manager | Service writes business event industry_project_archive | Required | Soft archive: is_active=false, archived_at set | Sample | Reversible in DB but no visible restore endpoint confirmed. |
| P1 | `vkpi_industry_automation.py` | `POST /industry-data/projects/{project_id}/accounts` | write | No explicit business audit in add_account found | Optional | Manual deactivate/edit | Sample | Creates/updates account and can preserve raw_platform_data. Needs audit if used by staff. |
| P1 | `vkpi_industry_automation.py` | `POST /industry-data/projects/{project_id}/accounts/import` | write | Per-row add_account has no explicit audit; aggregate audit not found | Required for large imports | Manual cleanup/deactivate | Yes | Bulk write path. Needs count-level audit before broad team use. |
| P1 | `vkpi_industry_automation.py` | `POST /industry-data/projects/{project_id}/apify/import` | write | Service writes business event industry_apify_import | Required | Manual cleanup; snapshots/posts may need purge | Yes | Bulk historical import writes accounts/snapshots/posts. Audit exists but rollback is not one-click. |
| P1 | `vkpi_industry_automation.py` | `PATCH /industry-data/accounts/{account_id}` | write | Service writes business event industry_account_update | Required if enabling crawl | Manual revert | Yes | Can set crawl_enabled and queue status. This is the UI close/open crawl path. |
| P1 | `vkpi_industry_automation.py` | `POST /industry-data/accounts/{account_id}/refresh` | write | Collector updates sync_status, crawl counters, snapshots/posts; no explicit business audit found | Recommended | No automatic rollback; new snapshots/posts persist | Yes | External API path. Real sample account 743 has crawl_error_count=8, last_successful_at=2026-05-11T06:25:13Z. |
| P2 | `vkpi_industry_automation.py` | `POST /audience-graph/estimate` | write + manager | Current service returns disabled/not_configured, no durable mutation | No | N/A | No | Safe while disabled. Re-evaluate when feature flag is enabled. |
| P1 | `vkpi_industry_automation.py` | `POST /automation/experiments` | admin | No explicit audit found; writes vkpi_scoring_experiments | Recommended | Manual status update/delete not confirmed | Sample | Admin-only ML experiment create. |
| P1 | `vkpi_industry_automation.py` | `PATCH /automation/experiments/{experiment_id}/status` | admin | No explicit audit found; writes experiment status | Recommended | Manual status update | Sample | Experiment status can change scoring behavior. |
| P0 | `vkpi_industry_automation.py` | `POST /automation/models/{model_version}/activate` | admin | No explicit audit found; updates all active models to registered, upserts active model | Required | Manual re-activate old model only if known | Yes | Global model switch with broad scoring impact and no explicit audit/rollback record. |
| P2 | `vkpi_industry_automation.py` | `POST /automation/ml/score` | write + manager | llm_gateway records vkpi_llm_calls/internal_ml not_configured | No | N/A | No | Currently fallback/not_configured scoring call; cost ledger should be reviewed in P4.6. |
| P1 | `vkpi_industry_automation.py` | `POST /automation/training-data/export` | admin | Writes vkpi_training_exports with status completed; no export audit found | Recommended | File cleanup/manual delete | Sample | Creates export file/record. Treat as sensitive data export path. |
| P1 | `vkpi_evidence_assets.py` | `POST /evidence/uploads` | write | Router writes filesystem only; no DB metadata/audit found at upload step | Recommended | Manual file delete only | Yes | File side effect under runtime/uploads, 25MB allow-list. Needs attachment linkage or upload audit for traceability. |
| P2 | `vkpi_evidence_assets.py` | `POST /messages` | write | Service writes business event evidence_message_create | No | No delete endpoint confirmed | No | Scoped to project/KOL/staff. Low destructive risk. |
| P2 | `vkpi_evidence_assets.py` | `POST /messages/{message_id}/attachments` | write | Service writes business event evidence_message_attachment_add | No | No delete endpoint confirmed | No | Attachment URL metadata write; upload itself is separate risk. |
| P2 | `vkpi_evidence_assets.py` | `POST /content` | write | Service writes business event evidence_content_upsert | No | Update/upsert can overwrite by URL | No | Scoped post evidence write. |
| P2 | `vkpi_evidence_assets.py` | `POST /content/{post_id}/assets` | write | Service writes business event evidence_content_asset_add | No | No delete endpoint confirmed | No | Media asset metadata write. |
| P1 | `vkpi_evidence_assets.py` | `POST /terms` | write | Service writes business event evidence_terms_upsert and workflow terms | Recommended | Overwrite with previous value if known | Sample | Business terms can affect deliverables/contract evidence; confirm for major terms edits. |
| P2 | `vkpi_evidence_assets.py` | `POST /deliverables` | write | Service writes business event evidence_deliverable_add | No | Update deliverable | No | Project-scoped deliverable create. |
| P2 | `vkpi_evidence_assets.py` | `PATCH /deliverables/{deliverable_id}` | write | Service writes business event evidence_deliverable_update | No | Manual update back | No | Project-scoped update. |
| P1 | `vkpi_evidence_assets.py` | `POST /shipments` | write | Service writes business event evidence_shipment_create | Recommended | Manual update status/metadata | Sample | Shipping/cost evidence path. Should be confirmed when cost/tracking is present. |
| P1 | `vkpi_evidence_assets.py` | `PATCH /shipments/{shipment_id}` | write | Service writes business event evidence_shipment_update | Recommended | Manual update back | Sample | Can update status/cost/received fields. |
| P1 | `vkpi_evidence_assets.py` | `POST /shipments/{shipment_id}/receive` | write | Delegates update_shipment; writes business event | Required | Manual status update back if allowed | Yes | State transition should have confirm or undo because it changes fulfillment state. |
| P1 | `vkpi_operations.py` | `POST /analytics/compare` | write | Writes vkpi_analytics_runs status/raw; no explicit business audit found | No | Run history only | Sample | External/provider analysis run, cost/latency side effect. |
| P1 | `vkpi_operations.py` | `POST /analytics/monitor` | write | Writes analytics run + outreach suggestions; no explicit business audit found | Recommended | Disable/delete suggestions manually | Yes | Creates suggestions from provider output. This is Daily Top100 candidate-source root. |
| P1 | `vkpi_operations.py` | `POST /analytics/products` | write | No explicit audit found; writes monitored_products | Recommended | Disable product | Sample | Controls products that cron/morning_sync can monitor. |
| P1 | `vkpi_operations.py` | `DELETE /analytics/products/{product_sku}` | write | No explicit audit found; soft disables enabled=false | Recommended | Re-enable by upsert | Sample | Soft delete but no audit found. |
| P1 | `vkpi_operations.py` | `POST /analytics/daily-digest/generate` | write + manager if staff override | Writes digest tables; no explicit business audit found | Recommended | Regenerate/overwrite same date | Yes | Bulk staff assignment write. P3/P4 Daily Top100 correctness depends on this path. |
| P1 | `vkpi_operations.py` | `POST /analytics/suggestions/{suggestion_id}/claim` | write | Service writes business event outreach_suggestion_claim | Recommended | Manual release claim/rollback not one-click | Sample | Creates/links KOL claim. Needs confirmation in UI for business workflow. |
| P1 | `vkpi_operations.py` | `POST /analytics/suggestions/{suggestion_id}/create-project` | write | Writes stage event + business event; may create short link | Required | Project cancel/delete and link cleanup manual | Yes | Multi-step operation: claim, project, optional Shopify short link. |
| P2 | `vkpi_operations.py` | `POST /analytics/suggestions/{suggestion_id}/dismiss` | write | No explicit audit found; sets dismissed fields | Optional | No restore endpoint confirmed | No | Low-risk workflow state, but no undo visible. |
| P1 | `vkpi_operations.py` | `POST /channels` | write | Service writes vkpi_channel_audit action=bind | Recommended | Unbind channel | Sample | Links external channel identity to staff; audit exists. |
| P1 | `vkpi_operations.py` | `DELETE /channels/{channel_id}` | write | Service writes vkpi_channel_audit action=unbind; soft revoked/deleted_at | Recommended | Re-bind channel | Sample | Identity/channel unlink; scoped access enforced. |
| P1 | `vkpi_operations.py` | `POST /channels/{channel_id}/sync-now` | write | Service writes vkpi_channel_audit action=sync_skipped currently | No | N/A | Sample | External sync path; currently mostly not_configured but should stay audited. |
| P1 | `vkpi_operations.py` | `POST /campaigns` | write + manager | No explicit audit found; writes vkpi_campaigns | Recommended | Manual archive/delete not confirmed | Sample | Campaign state create without audit. |
| P1 | `vkpi_operations.py` | `POST /campaigns/{campaign_id}/projects` | write + manager | No explicit audit found; insert-or-ignore bridge table | Recommended | No remove endpoint confirmed | Sample | Can affect reporting grouping; needs audit/remove later. |
| P0 | `vkpi_operations.py` | `POST /budget-pools` | admin | No explicit audit found; writes budget pool | Required | No close/archive endpoint confirmed | Yes | Financial budget object creation with no business audit found. |
| P0 | `vkpi_operations.py` | `POST /budget-pools/{pool_id}/allocate` | admin | No explicit audit found; writes budget allocation | Required | No void/reverse endpoint confirmed | Yes | Financial allocation. Needs audit, confirmation, and reversal path. |
| P0 | `vkpi_operations.py` | `POST /staff/{staff_id}/offboard/initiate` | admin | Creates vkpi_offboarding_runs pending; no business audit found | Required | Delete/cancel endpoint not confirmed | Yes | Prepares staff offboarding; low direct mutation but sensitive HR/business action. |
| P0 | `vkpi_operations.py` | `POST /offboarding/{run_id}/execute` | admin | Updates claims/projects/channels; stores result_json; no business audit found | Required double-confirm | Partial rollback manual only | Yes | Highest-risk multi-table mutation. Result log exists but no explicit audit/rollback transaction plan. |
| P0 | `vkpi_operations.py` | `POST /cron/{job_name}/run` | admin | No explicit audit found; cron can run lineage/kpi/alerts/report/monitor/channel sync/morning sync | Required | Job-specific; no generic rollback | Yes | Admin endpoint can trigger broad jobs with provider calls and bulk writes. |

## Launch Split

### Launch-Before

- Close P0 rows or explicitly document them in P4.8a risk notes if not fixed before launch.
- Complete P4.2C real QA for all P0 rows and at least representative P1 rows from each first-tier router.
- For `vkpi_settings` and `vkpi_operations`, verify browser confirmation/visible before-after state because backend already has mixed audit coverage but UX confirmation is unknown from static code.

### Launch-After

- Add or normalize lower-risk business audit for P1 rows that currently only write state tables.
- Add restore/undo endpoints for soft-dismiss, campaign-project link removal, and evidence attachment removal if real users request them.
- Fold this matrix into the wider P4 mutation-safety audit after second-tier routers are reviewed.

