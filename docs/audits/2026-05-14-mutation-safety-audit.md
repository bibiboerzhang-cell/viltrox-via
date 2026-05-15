# V-KPI Mutation Safety Audit

- Date: 2026-05-14
- Scope: `backend/app/api/routers/*.py` write routes (`POST/PATCH/PUT/DELETE`)
- Mode: static scan only, no feature code changed
- Important: `audit_static=false` means the router/function body did not visibly call audit; service-layer audit may still exist and must be verified before calling it a defect.

## Summary

- Mutation endpoints scanned: **296**
- Static permission guard found: **256**
- Static audit found: **15**
- Static firewall decorator found: **1**
- Confirmation / reason / force / dry-run hint found: **40**

## Risk Buckets

| Bucket | Count | Meaning | Action |
|---|---:|---|---|
| P0-auth-unknown | 40 | Static scan did not see `require_*` dependency in route body/decorators | Verify auth source first; many non-VKPI public/account routes may be false positives |
| P1-audit-unverified | 64 | Dangerous mutation has permission but no visible route-level audit | Check service-level audit before patching |
| P2-confirm-unverified | 9 | Dangerous mutation has permission/audit but no static confirmation hint | Browser UX/confirm audit later |
| P3-static-ok | 183 | Static route surface has enough signals for now | No action in this pass |

## Router Distribution

| Router | Mutations | P0 | P1 | P2 | P3 |
|---|---:|---:|---:|---:|---:|
| `account_scanner.py` | 2 | 0 | 0 | 0 | 2 |
| `activities.py` | 5 | 1 | 0 | 0 | 4 |
| `admin.py` | 37 | 5 | 8 | 1 | 23 |
| `audit.py` | 3 | 3 | 0 | 0 | 0 |
| `auth.py` | 8 | 8 | 0 | 0 | 0 |
| `brand_analysis.py` | 1 | 1 | 0 | 0 | 0 |
| `commerce.py` | 10 | 0 | 2 | 0 | 8 |
| `creator.py` | 7 | 7 | 0 | 0 | 0 |
| `deepsight.py` | 4 | 0 | 0 | 0 | 4 |
| `intelligence.py` | 20 | 0 | 2 | 0 | 18 |
| `intelligence_admin.py` | 3 | 0 | 2 | 0 | 1 |
| `kol_ops.py` | 11 | 0 | 3 | 0 | 8 |
| `kol_ops_content.py` | 8 | 0 | 0 | 0 | 8 |
| `platform_ingest.py` | 1 | 1 | 0 | 0 | 0 |
| `student_identity.py` | 3 | 3 | 0 | 0 | 0 |
| `system_admin.py` | 28 | 1 | 3 | 0 | 24 |
| `uploads.py` | 2 | 1 | 1 | 0 | 0 |
| `verify.py` | 7 | 0 | 1 | 0 | 6 |
| `via.py` | 6 | 6 | 0 | 0 | 0 |
| `vkpi.py` | 3 | 3 | 0 | 0 | 0 |
| `vkpi_attribution_metrics.py` | 9 | 0 | 4 | 0 | 5 |
| `vkpi_comment_intelligence.py` | 3 | 0 | 0 | 0 | 3 |
| `vkpi_comments.py` | 2 | 0 | 0 | 0 | 2 |
| `vkpi_costs.py` | 4 | 0 | 4 | 0 | 0 |
| `vkpi_data_quality.py` | 6 | 0 | 0 | 0 | 6 |
| `vkpi_evidence_assets.py` | 11 | 0 | 1 | 0 | 10 |
| `vkpi_feedback.py` | 2 | 0 | 0 | 0 | 2 |
| `vkpi_firewall.py` | 3 | 0 | 0 | 3 | 0 |
| `vkpi_industry_automation.py` | 13 | 0 | 4 | 0 | 9 |
| `vkpi_kol_links.py` | 12 | 0 | 6 | 0 | 6 |
| `vkpi_kol_pool.py` | 5 | 0 | 0 | 4 | 1 |
| `vkpi_operations.py` | 18 | 0 | 7 | 0 | 11 |
| `vkpi_pillars.py` | 3 | 0 | 0 | 0 | 3 |
| `vkpi_product_analysis.py` | 6 | 0 | 2 | 0 | 4 |
| `vkpi_projects.py` | 10 | 0 | 2 | 0 | 8 |
| `vkpi_reconciliation.py` | 5 | 0 | 1 | 0 | 4 |
| `vkpi_reports.py` | 2 | 0 | 2 | 0 | 0 |
| `vkpi_sentiment.py` | 3 | 0 | 0 | 0 | 3 |
| `vkpi_settings.py` | 7 | 0 | 7 | 0 | 0 |
| `vkpi_sync.py` | 1 | 0 | 0 | 1 | 0 |
| `vkpi_weekly_reports.py` | 2 | 0 | 2 | 0 | 0 |

## P0 Auth-Unknown Candidates

These are not automatically bugs. Some are intentionally public auth/upload endpoints; some may rely on router-level or service-level protection that static scan did not infer.

| Method | Path | Function | File |
|---|---|---|---|
| POST | `/api/admin/activities/{qr_token}/track` | `track_public_event` | `backend/app/api/routers/activities.py` |
| POST | `/api/admin/redemptions/{rid}/approve` | `admin_approve_redemption` | `backend/app/api/routers/admin.py` |
| POST | `/api/admin/redemptions/{rid}/pack` | `admin_pack_redemption` | `backend/app/api/routers/admin.py` |
| POST | `/api/admin/redemptions/{rid}/ship` | `admin_ship_redemption` | `backend/app/api/routers/admin.py` |
| POST | `/api/admin/redemptions/{rid}/deliver` | `admin_deliver_redemption` | `backend/app/api/routers/admin.py` |
| POST | `/api/admin/redemptions/{rid}/reject` | `admin_reject_redemption` | `backend/app/api/routers/admin.py` |
| POST | `/api/audit` | `audit_async` | `backend/app/api/routers/audit.py` |
| POST | `/api/audit/v2` | `audit_async` | `backend/app/api/routers/audit.py` |
| POST | `/api/audit/sync` | `audit_sync` | `backend/app/api/routers/audit.py` |
| POST | `/api/auth/register` | `auth_register` | `backend/app/api/routers/auth.py` |
| POST | `/api/auth/login` | `auth_login` | `backend/app/api/routers/auth.py` |
| POST | `/api/auth/me/avatar` | `update_my_avatar` | `backend/app/api/routers/auth.py` |
| POST | `/api/auth/logout` | `auth_logout` | `backend/app/api/routers/auth.py` |
| POST | `/api/auth/resend-verification` | `resend_verification` | `backend/app/api/routers/auth.py` |
| POST | `/api/auth/forgot-password` | `forgot_password` | `backend/app/api/routers/auth.py` |
| POST | `/api/auth/reset-password` | `reset_password_endpoint` | `backend/app/api/routers/auth.py` |
| POST | `/api/auth/change-password` | `change_password` | `backend/app/api/routers/auth.py` |
| POST | `/api/admin/intel/analyze-brand` | `analyze_brand` | `backend/app/api/routers/brand_analysis.py` |
| POST | `/api/creator/social-accounts` | `add_social_account` | `backend/app/api/routers/creator.py` |
| DELETE | `/api/creator/social-accounts/{account_id}` | `delete_social_account` | `backend/app/api/routers/creator.py` |
| POST | `/api/creator/addresses` | `creator_add_address` | `backend/app/api/routers/creator.py` |
| POST | `/api/creator/redeem` | `creator_redeem` | `backend/app/api/routers/creator.py` |
| PATCH | `/api/creator/profile` | `update_profile` | `backend/app/api/routers/creator.py` |
| DELETE | `/api/creator/addresses/{address_id}` | `delete_address` | `backend/app/api/routers/creator.py` |
| PATCH | `/api/creator/addresses/{address_id}/default` | `set_default_address` | `backend/app/api/routers/creator.py` |
| POST | `/api/platform-ingest/{platform}/webhook` | `ingest_platform_webhook` | `backend/app/api/routers/platform_ingest.py` |
| POST | `/api/creator-public/click` | `creator_public_click` | `backend/app/api/routers/student_identity.py` |
| POST | `/api/student/signup` | `student_signup` | `backend/app/api/routers/student_identity.py` |
| POST | `/api/student/pass/check-in` | `student_pass_check_in` | `backend/app/api/routers/student_identity.py` |
| POST | `/api/admin/staff/accept-invite` | `accept_staff_invite` | `backend/app/api/routers/system_admin.py` |
| POST | `/api/upload/video` | `upload_video` | `backend/app/api/routers/uploads.py` |
| POST | `/api/via/sessions` | `create_via_session` | `backend/app/api/routers/via.py` |
| PATCH | `/api/via/sessions/{session_key}/persona` | `patch_via_persona` | `backend/app/api/routers/via.py` |
| POST | `/api/via/sessions/{session_key}/memory/refresh` | `refresh_via_memory` | `backend/app/api/routers/via.py` |
| POST | `/api/via/sessions/{session_key}/events` | `post_via_event` | `backend/app/api/routers/via.py` |
| POST | `/api/via/sessions/{session_key}/respond` | `respond_via_session` | `backend/app/api/routers/via.py` |
| POST | `/api/via/sessions/{session_key}/reward-traces` | `post_via_reward_trace` | `backend/app/api/routers/via.py` |
| POST | `/api/admin/vkpi/shopify/orders` | `shopify_order_webhook` | `backend/app/api/routers/vkpi.py` |
| POST | `/api/admin/vkpi/shopify` | `shopify_order_webhook` | `backend/app/api/routers/vkpi.py` |
| POST | `/api/admin/vkpi/shopify/refunds` | `shopify_refund_webhook` | `backend/app/api/routers/vkpi.py` |

## P1 Audit-Unverified Candidates

These have a permission signal and look operationally dangerous, but route-level audit is not visible. Next pass should verify service-level audit before adding code.

| Method | Path | Function | Confirm hint | File |
|---|---|---|---|---|
| POST | `/api/admin/users/{uid}/approve` | `admin_approve_user` | no | `backend/app/api/routers/admin.py` |
| DELETE | `/api/admin/users/{uid}` | `admin_delete_user` | no | `backend/app/api/routers/admin.py` |
| DELETE | `/api/admin/creator-public/shop-heroes/{hero_id}` | `admin_delete_creator_shop_hero` | no | `backend/app/api/routers/admin.py` |
| DELETE | `/api/admin/submissions/{submission_id}` | `delete_submission` | yes | `backend/app/api/routers/admin.py` |
| POST | `/api/admin/verifications/{ver_id}/approve` | `approve_verification` | no | `backend/app/api/routers/admin.py` |
| DELETE | `/api/admin/verifications/{ver_id}` | `delete_verification` | no | `backend/app/api/routers/admin.py` |
| POST | `/api/admin/rewards/{rid}/archive` | `admin_archive_reward` | no | `backend/app/api/routers/admin.py` |
| DELETE | `/api/admin/rewards/{rid}` | `admin_delete_reward` | no | `backend/app/api/routers/admin.py` |
| POST | `/api/admin/payouts/cycle/{cycle_id}/approve-all` | `approve_all_in_cycle` | no | `backend/app/api/routers/commerce.py` |
| POST | `/api/admin/payouts/{payout_id}/approve` | `approve_payout` | no | `backend/app/api/routers/commerce.py` |
| POST | `/api/admin/intel/via/proposals/{proposal_key}/approve` | `via_proposal_approve` | no | `backend/app/api/routers/intelligence.py` |
| POST | `/api/admin/intel/via/policies/{version_key}/promote` | `via_policy_promote` | yes | `backend/app/api/routers/intelligence.py` |
| POST | `/api/intelligence/market/gaps/generate` | `generate_market_gaps` | yes | `backend/app/api/routers/intelligence_admin.py` |
| POST | `/api/intelligence/brand/insights/generate` | `generate_brand_insights` | no | `backend/app/api/routers/intelligence_admin.py` |
| POST | `/api/admin/kol/candidates/{candidate_id}/promote` | `promote_candidate` | no | `backend/app/api/routers/kol_ops.py` |
| DELETE | `/api/admin/kol/kols/{kol_id}` | `delete_kol` | yes | `backend/app/api/routers/kol_ops.py` |
| POST | `/api/admin/kol/kols/import-csv` | `import_kols_csv` | no | `backend/app/api/routers/kol_ops.py` |
| POST | `/api/admin/system/providers/{provider}/probe` | `probe_provider` | no | `backend/app/api/routers/system_admin.py` |
| POST | `/api/admin/staff/{staff_id}/resend-invite` | `resend_staff_invite` | no | `backend/app/api/routers/system_admin.py` |
| DELETE | `/api/admin/staff/{staff_id}` | `delete_staff_member` | no | `backend/app/api/routers/system_admin.py` |
| POST | `/api/admin/upload/reward-image` | `admin_upload_reward_image` | no | `backend/app/api/routers/uploads.py` |
| POST | `/api/verify/{verification_id}/approve` | `admin_approve` | no | `backend/app/api/routers/verify.py` |
| POST | `/api/admin/vkpi/attribution/amazon/import` | `import_amazon` | no | `backend/app/api/routers/vkpi_attribution_metrics.py` |
| POST | `/api/admin/vkpi/attribution/amazon/upload` | `upload_amazon_report` | no | `backend/app/api/routers/vkpi_attribution_metrics.py` |
| POST | `/api/admin/vkpi/shopify/sync` | `shopify_sync` | no | `backend/app/api/routers/vkpi_attribution_metrics.py` |
| POST | `/api/admin/vkpi/alerts/generate` | `generate_alerts` | no | `backend/app/api/routers/vkpi_attribution_metrics.py` |
| PATCH | `/api/admin/vkpi/costs/{cost_id}` | `update_cost` | no | `backend/app/api/routers/vkpi_costs.py` |
| POST | `/api/admin/vkpi/costs/{cost_id}/approve` | `approve_cost` | no | `backend/app/api/routers/vkpi_costs.py` |
| POST | `/api/admin/vkpi/costs/{cost_id}/void` | `void_cost` | no | `backend/app/api/routers/vkpi_costs.py` |
| POST | `/api/admin/vkpi/product-costs` | `upsert_product_cost` | no | `backend/app/api/routers/vkpi_costs.py` |
| POST | `/api/admin/vkpi/evidence/uploads` | `upload_evidence_file` | no | `backend/app/api/routers/vkpi_evidence_assets.py` |
| DELETE | `/api/admin/vkpi/industry-data/projects/{project_id}` | `industry_delete_project` | no | `backend/app/api/routers/vkpi_industry_automation.py` |
| POST | `/api/admin/vkpi/industry-data/projects/{project_id}/accounts/import` | `industry_import_accounts` | no | `backend/app/api/routers/vkpi_industry_automation.py` |
| POST | `/api/admin/vkpi/industry-data/projects/{project_id}/apify/import` | `industry_import_apify_history` | no | `backend/app/api/routers/vkpi_industry_automation.py` |
| POST | `/api/admin/vkpi/automation/training-data/export` | `automation_training_export` | no | `backend/app/api/routers/vkpi_industry_automation.py` |
| POST | `/api/admin/vkpi/claims/{claim_id}/reassign` | `reassign_claim` | no | `backend/app/api/routers/vkpi_kol_links.py` |
| POST | `/api/admin/vkpi/links` | `create_link` | no | `backend/app/api/routers/vkpi_kol_links.py` |
| PATCH | `/api/admin/vkpi/links/{link_id}` | `update_link` | no | `backend/app/api/routers/vkpi_kol_links.py` |
| POST | `/api/admin/vkpi/links/{link_id}/pause` | `pause_link` | no | `backend/app/api/routers/vkpi_kol_links.py` |
| POST | `/api/admin/vkpi/links/{link_id}/archive` | `archive_link` | no | `backend/app/api/routers/vkpi_kol_links.py` |
| POST | `/api/admin/vkpi/links/{link_id}/health-check` | `check_link` | no | `backend/app/api/routers/vkpi_kol_links.py` |
| DELETE | `/api/admin/vkpi/analytics/products/{product_sku}` | `analytics_product_delete` | no | `backend/app/api/routers/vkpi_operations.py` |
| POST | `/api/admin/vkpi/analytics/daily-digest/generate` | `analytics_daily_digest_generate` | no | `backend/app/api/routers/vkpi_operations.py` |
| POST | `/api/admin/vkpi/channels/{channel_id}/sync-now` | `sync_channel` | no | `backend/app/api/routers/vkpi_operations.py` |
| POST | `/api/admin/vkpi/budget-pools` | `create_budget_pool` | no | `backend/app/api/routers/vkpi_operations.py` |
| POST | `/api/admin/vkpi/budget-pools/{pool_id}/allocate` | `allocate_budget` | no | `backend/app/api/routers/vkpi_operations.py` |
| POST | `/api/admin/vkpi/staff/{staff_id}/offboard/initiate` | `offboarding_initiate` | no | `backend/app/api/routers/vkpi_operations.py` |
| POST | `/api/admin/vkpi/offboarding/{run_id}/execute` | `offboarding_execute` | no | `backend/app/api/routers/vkpi_operations.py` |
| DELETE | `/api/admin/vkpi/product-analysis/launches/{launch_id}` | `product_analysis_delete_launch` | no | `backend/app/api/routers/vkpi_product_analysis.py` |
| POST | `/api/admin/vkpi/product-analysis/kol-pool/import` | `product_analysis_import_kol_pool` | no | `backend/app/api/routers/vkpi_product_analysis.py` |
| DELETE | `/api/admin/vkpi/projects/{project_id}` | `delete_project` | no | `backend/app/api/routers/vkpi_projects.py` |
| POST | `/api/admin/vkpi/projects/{project_id}/costs` | `add_project_cost` | no | `backend/app/api/routers/vkpi_projects.py` |
| POST | `/api/admin/vkpi/attribution/{attribution_id}/void` | `attribution_void` | no | `backend/app/api/routers/vkpi_reconciliation.py` |
| POST | `/api/admin/vkpi/reports/weekly/generate` | `generate_weekly_report` | no | `backend/app/api/routers/vkpi_reports.py` |
| POST | `/api/admin/vkpi/exports/{export_format}` | `create_export` | no | `backend/app/api/routers/vkpi_reports.py` |
| POST | `/api/admin/vkpi/settings/providers/{provider}/probe` | `provider_probe` | no | `backend/app/api/routers/vkpi_settings.py` |
| PATCH | `/api/admin/vkpi/settings/feature-flags` | `update_feature_flags` | no | `backend/app/api/routers/vkpi_settings.py` |
| PATCH | `/api/admin/vkpi/settings/platform-crawl` | `update_platform_crawl` | no | `backend/app/api/routers/vkpi_settings.py` |
| PATCH | `/api/admin/vkpi/settings/budgets` | `update_budget_settings` | no | `backend/app/api/routers/vkpi_settings.py` |
| PATCH | `/api/admin/vkpi/settings/comment-alerts` | `update_comment_alert_settings` | no | `backend/app/api/routers/vkpi_settings.py` |
| PATCH | `/api/admin/vkpi/settings/preferences` | `update_preference_settings` | no | `backend/app/api/routers/vkpi_settings.py` |
| PATCH | `/api/admin/vkpi/settings/notifications` | `update_notifications` | no | `backend/app/api/routers/vkpi_settings.py` |
| POST | `/generate-for-staff/{staff_id}` | `api_generate_for_staff` | no | `backend/app/api/routers/vkpi_weekly_reports.py` |
| POST | `/generate-all` | `api_generate_all` | no | `backend/app/api/routers/vkpi_weekly_reports.py` |

## P2 Confirm-Unverified Candidates

These have visible audit but no static confirmation/reason/dry-run hint. This is usually a UX/browser QA issue, not a backend blocker.

| Method | Path | Function | File |
|---|---|---|---|
| DELETE | `/api/admin/learning/corrections` | `delete_correction_endpoint` | `backend/app/api/routers/admin.py` |
| POST | `/api/admin/vkpi/settings/firewall/feature-flags` | `update_feature_flag` | `backend/app/api/routers/vkpi_firewall.py` |
| POST | `/api/admin/vkpi/settings/firewall/platform/{platform}` | `update_platform` | `backend/app/api/routers/vkpi_firewall.py` |
| POST | `/api/admin/vkpi/settings/firewall/budget/{budget_key}` | `update_budget` | `backend/app/api/routers/vkpi_firewall.py` |
| POST | `/api/admin/vkpi/kol-pool/batch-enrich` | `batch_enrich_pool_items` | `backend/app/api/routers/vkpi_kol_pool.py` |
| POST | `/api/admin/vkpi/kol-pool/{kol_pool_id}/promote` | `promote_to_main_kol` | `backend/app/api/routers/vkpi_kol_pool.py` |
| POST | `/api/admin/vkpi/kol-pool/{kol_pool_id}/enrich` | `enrich_pool_item` | `backend/app/api/routers/vkpi_kol_pool.py` |
| POST | `/api/admin/vkpi/kol-pool/{kol_pool_id}/link` | `link_to_main_kol` | `backend/app/api/routers/vkpi_kol_pool.py` |
| POST | `/api/admin/vkpi/sync/trigger/{job_name}` | `trigger_sync` | `backend/app/api/routers/vkpi_sync.py` |

## Immediate Next Actions

1. Do not patch all P1 rows blindly; first sample-check `vkpi_costs.py`, `vkpi_settings.py`, `vkpi_projects.py`, and `vkpi_kol_links.py` service calls for existing audit writes.
2. Treat `P0-auth-unknown` outside `/api/admin/vkpi` separately; public creator/auth flows have different risk rules.
3. For V-KPI admin money/data mutations, target a small hardening pass: Settings budgets/crawl, Costs approve/void/edit, Project delete, Link archive/pause, Report export/generate.
4. Browser QA should verify confirmation dialogs and visible rollback/void states for only those high-risk actions, not every button in the product.

