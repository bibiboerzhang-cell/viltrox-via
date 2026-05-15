# V-KPI Service-Layer Audit Verification

Date: 2026-05-14
Scope: Step 11, report-only verification for high-risk V-KPI mutation modules.

## Scope

Verified four modules that were high-signal in the static Button Truth / Mutation Safety scan:

- `backend/app/api/routers/vkpi_costs.py` + `backend/app/services/vkpi/costs.py`
- `backend/app/api/routers/vkpi_settings.py` + `backend/app/services/vkpi/platform_crawl_settings.py`, `user_preferences.py`, `notification_settings.py`
- `backend/app/api/routers/vkpi_projects.py` + `workflow_projects.py`, `workflow_evidence.py`
- `backend/app/api/routers/vkpi_kol_links.py` + `kol_claims_actions.py`, `link_center.py`

This pass checks whether static `audit_static=false` items are actually covered in service code. No product code was changed.

## Summary

| Area | Permission / Scope | Service Audit | Rollback / Reversal | Verdict |
|---|---:|---:|---:|---|
| Costs ledger | Yes | Yes | Yes for void lifecycle | Safe |
| Settings / budgets / crawl controls | Yes | Yes when actor exists | No explicit rollback, audit trail only | Needs confirm UX later |
| Projects core lifecycle | Yes | Domain stage event exists, but no `vkpi_business_audit_logs` for create/transition/delete | Soft delete only | Gap: audit table parity |
| Project evidence / content / shipments | Yes | Yes | Upsert / append only, no delete path observed | Mostly safe |
| KOL claims lifecycle | Partial / Yes | No explicit business audit for claim/release/reassign/update manual | Release/reassign status history exists | Gap: audit table parity |
| Short links | Yes | Yes | Status pause/archive/resume via update path | Safe |

## Findings By Module

### 1. Costs

Evidence:

- `add_cost` calls `scope.assert_project_access(..., write=True)` and `audit.log_business_event(action_type="cost_add")`.
- `update_cost` calls `scope.assert_project_access(..., write=True)` and `audit.log_business_event(action_type="cost_edit")`.
- `approve_cost` writes `approved_by_staff_id`, `approved_at`, and `audit.log_business_event(action_type="cost_approve")`.
- `void_cost` writes `status='void'`, `voided_by_staff_id`, `voided_at`, appends reason into `note`, and writes `audit.log_business_event(action_type="cost_void")`.
- Step 15 added audit parity for `upsert_product_cost` with `audit.log_business_event(action_type="product_cost_upsert")`.
- Step 15 added audit parity for `record_shipped_product_cost` with `audit.log_business_event(action_type="cost_add")`.

Verdict:

- Operational cost ledger and product cost catalog writes now have service-layer audit coverage.
- Auto product cost posting is idempotent and now writes a centralized business audit event on the first successful insert.

### 2. Settings / Budgets / Crawl Controls

Evidence:

- `update_comment_alert_settings` writes `audit.log_settings_change(change_type="comment_alert_threshold")`.
- `update_feature_flags` writes `audit.log_settings_change(change_type="feature_flag")`.
- `update_platform_settings` writes `audit.log_settings_change(change_type="platform_crawl")`.
- `update_budget_settings` writes `audit.log_settings_change(change_type="budget_setting")`.
- `update_preferences` and `update_notification_settings` both write settings audit and business audit events.

Gaps:

- Audit is conditional on `actor` being truthy. Router paths supply authenticated staff, so normal UI/API calls are covered. Scripts that pass `staff=None` will not write audit.
- Budget / platform crawl mutations do not have an explicit rollback endpoint; recovery is by applying a new settings update based on audit history.
- UI confirmation is a separate frontend concern and was not verified in this service pass.

Verdict:

- Service-layer audit exists.
- P4 follow-up should focus on confirmation UX and script discipline (`--staff-id` or explicit no-audit flag), not new backend audit primitives.

### 3. Projects Core Lifecycle

Evidence:

- `create_project` writes `vkpi_project_stage_events(event_type='created')`.
- `transition_project` calls `scope.assert_project_access(..., write=True)` and writes `vkpi_project_stage_events` for stage changes.
- `delete_project` is soft delete: sets `stage_status='deleted'`, writes deletion metadata in `metadata_json`, inserts a `vkpi_project_stage_events(event_type='deleted')`, and pauses live project links.

Gaps:

- `create_project`, `transition_project`, and `delete_project` do not call `audit.log_business_event`.
- Stage events provide domain traceability but are not in the centralized `vkpi_business_audit_logs` surface.
- Soft delete exists, but there is no observed restore endpoint in this verification scope.

Verdict:

- Not a fake or unsafe path. It has scope checks and domain event history.
- For P4 governance, project core lifecycle should get audit-table parity so admin audit views show the same lifecycle changes as project detail.

### 4. Project Evidence / Content / Terms / Shipments

Evidence:

- `add_project_message` calls `scope.assert_project_access(..., write=True)` and writes `audit.log_business_event(action_type="message_capture")`.
- `add_project_content` writes `content_asset_add` and `content_capture` audit events.
- `upsert_project_terms` writes `audit.log_business_event(action_type="terms_upsert")`.
- `add_project_shipment` writes `audit.log_business_event(action_type="shipment_add")`.

Gaps:

- These paths are append/upsert focused. Delete / restore behavior was not present in this scope.
- File upload and attachment storage safety were not checked here.

Verdict:

- Service audit exists. These are not priority P0 fixes.

### 5. KOL Claims / Manual KOL Updates

Evidence:

- `claim` writes a row in `vkpi_kol_claims` and updates `kols.assigned_staff_id`.
- `release` marks the claim released, stores `release_reason`, `released_at`, `released_by_staff_id`, and clears assigned staff.
- `reassign` calls `release` then `claim` with reassignment metadata.
- `update_kol_manual` calls `assert_kol_access` before updating the `kols` row.

Gaps:

- `claim`, `release`, `reassign`, and `update_kol_manual` do not write centralized `audit.log_business_event` entries.
- `reassign` calls `claim(..., staff=None)` after release, so the new claim row stores target staff but not the acting admin as an audit event.
- `lookup(create_if_missing=True)` can create a KOL row without centralized audit.

Verdict:

- Business state history exists in claim rows, but admin audit visibility is incomplete.
- This is the clearest P4 governance gap found in Step 11.

### 6. Short Links

Evidence:

- `create_link` validates destination/fallback URLs, applies scope/project access, and calls `_log_link_event(action_type="link_create")`.
- `update_link` calls `scope.assert_link_access(..., write=True)` and `_log_link_event(action_type="link_update")`.
- `set_status` maps pause/archive/resume into audited update actions.
- `health_check` writes health status and `_log_link_event(action_type="link_health_check")`.

Gaps:

- No hard delete path observed; lifecycle uses status changes.

Verdict:

- Safe enough. Do not patch unless UI confirmation is needed later.

## Actionable Next Steps

1. Do not blindly patch all static P1 endpoints. Many are already audited in service code.
2. Add a narrow P4 governance task for project core lifecycle audit-table parity:
   - `project_create`
   - `project_stage_transition`
   - `project_delete`
3. Add a narrow P4 governance task for KOL lifecycle audit-table parity:
   - `kol_lookup_create`
   - `kol_claim`
   - `kol_release`
   - `kol_reassign`
   - `kol_manual_update`
4. Keep settings/crawl/budget as service-audited; next improvement should be UI confirmation and script audit discipline, not new backend audit primitives.
5. Cost ledger and product cost catalog audit parity are complete as of Step 15.

## P4 Risk Reclassification

| Previous static risk | After service verification |
|---|---|
| Settings write APIs: P1 audit unknown | Downgrade to P3 UX/script discipline |
| Costs mutations: P1 audit unknown | Closed in Step 15; remaining work is UI confirmation / reporting only |
| Project lifecycle: P1 audit unknown | Keep P1: audit table parity needed |
| Project evidence: P1 audit unknown | Downgrade to P3 |
| KOL lifecycle: P1 audit unknown | Keep P1: audit table parity needed |
| Link mutations: P1 audit unknown | Downgrade to P3 |

## Acceptance

This report is complete when:

- The report exists under `docs/audits/`.
- No feature code was changed for Step 11.
- Boundary check passes.
- Full pytest remains green.
