# Backend Mutation Safety Audit - 2026-05-14
范围: 静态扫描 `backend/app/api/routers/*.py` 中所有 FastAPI `POST/PUT/PATCH/DELETE` 写接口。只输出报告,不修改功能代码。
重要限制: 本报告是静态审计。Router 层没有看到 audit/rollback 不代表 service 层一定没有,下一步需要对 P0/P1 端点做真实 HTTP 动态验证。
风险定义: P0=高影响且静态缺口明显; P1=有 guard 但审计/确认/回滚需验证; P2=低风险或已有明显治理线索。

## Summary
- 写接口总数: 296
- 方法分布: DELETE=16, PATCH=25, POST=251, PUT=4
- 风险分布: P0=81, P1=171, P2=44
- 无明显权限 guard: 19
- 无明显 audit hint: 238
- P0 且无确认/原因 hint: 68
- DELETE 且无明显软删/回滚 hint: 3
- 全量矩阵 CSV: `docs/audits/2026-05-14-mutation-safety-matrix.csv`

## P0 Findings - 优先动态验证
| Risk | Method | Endpoint | Function | File | Issue |
|---|---:|---|---|---|---|
| P0 | POST | `/api/admin/social-accounts/{account_id}/reject` | `admin_reject_social` | `backend/app/api/routers/admin.py:580` | high-impact mutation (delete); missing/unclear: audit |
| P0 | POST | `/api/admin/users/{uid}/block` | `admin_block_user` | `backend/app/api/routers/admin.py:651` | high-impact mutation (block); missing/unclear: audit |
| P0 | POST | `/api/admin/users/{uid}/unblock` | `admin_unblock_user` | `backend/app/api/routers/admin.py:662` | high-impact mutation (block); missing/unclear: audit |
| P0 | DELETE | `/api/admin/users/{uid}` | `admin_delete_user` | `backend/app/api/routers/admin.py:673` | high-impact mutation (delete); missing/unclear: audit, confirm/reason |
| P0 | DELETE | `/api/admin/creator-public/shop-heroes/{hero_id}` | `admin_delete_creator_shop_hero` | `backend/app/api/routers/admin.py:885` | high-impact mutation (delete); missing/unclear: audit, confirm/reason |
| P0 | DELETE | `/api/admin/submissions/{submission_id}` | `delete_submission` | `backend/app/api/routers/admin.py:961` | high-impact mutation (delete); missing/unclear: audit |
| P0 | DELETE | `/api/admin/verifications/{ver_id}` | `delete_verification` | `backend/app/api/routers/admin.py:1091` | high-impact mutation (delete); missing/unclear: audit, confirm/reason |
| P0 | DELETE | `/api/admin/rewards/{rid}` | `admin_delete_reward` | `backend/app/api/routers/admin.py:1446` | high-impact mutation (delete); missing/unclear: audit, confirm/reason |
| P0 | POST | `/api/admin/users/{uid}/adjust_points` | `admin_adjust_points` | `backend/app/api/routers/admin.py:1615` | high-impact mutation (adjust_points); missing/unclear: audit |
| P0 | POST | `/api/admin/users/{uid}/grant_points` | `admin_grant_points` | `backend/app/api/routers/admin.py:1963` | high-impact mutation (grant_points); missing/unclear: audit |
| P0 | DELETE | `/api/admin/learning/corrections` | `delete_correction_endpoint` | `backend/app/api/routers/admin.py:2108` | high-impact mutation (delete); missing/unclear: confirm/reason |
| P0 | POST | `/me/avatar` | `update_my_avatar` | `backend/app/api/routers/auth.py:262` | high-impact mutation (secret); missing/unclear: audit, confirm/reason |
| P0 | POST | `/orders/backfill` | `backfill_orders` | `backend/app/api/routers/commerce.py:70` | high-impact mutation (backfill); missing/unclear: confirm/reason |
| P0 | POST | `/payouts/cycle/{cycle_id}/approve-all` | `approve_all_in_cycle` | `backend/app/api/routers/commerce.py:231` | high-impact mutation (approve-all); missing/unclear: confirm/reason |
| P0 | POST | `/payouts/cycle/{cycle_id}/process` | `process_cycle` | `backend/app/api/routers/commerce.py:242` | high-impact mutation (process); missing/unclear: confirm/reason |
| P0 | DELETE | `/social-accounts/{account_id}` | `delete_social_account` | `backend/app/api/routers/creator.py:296` | high-impact mutation (delete); missing/unclear: audit, confirm/reason |
| P0 | DELETE | `/addresses/{address_id}` | `delete_address` | `backend/app/api/routers/creator.py:448` | high-impact mutation (delete); missing/unclear: audit, confirm/reason |
| P0 | POST | `/cache/clear` | `cache_clear` | `backend/app/api/routers/deepsight.py:90` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/bh/refresh-now` | `bh_refresh_now` | `backend/app/api/routers/intelligence.py:320` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/via/learn-now` | `via_learn_now` | `backend/app/api/routers/intelligence.py:391` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/via/proposals/{proposal_key}/approve` | `via_proposal_approve` | `backend/app/api/routers/intelligence.py:443` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/via/proposals/{proposal_key}/reject` | `via_proposal_reject` | `backend/app/api/routers/intelligence.py:454` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/via/proposals/{proposal_key}/apply` | `via_proposal_apply` | `backend/app/api/routers/intelligence.py:465` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/via/proposals/{proposal_key}/stage` | `via_proposal_stage` | `backend/app/api/routers/intelligence.py:476` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/via/policies/{version_key}/promote` | `via_policy_promote` | `backend/app/api/routers/intelligence.py:487` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/via/policies/{version_key}/rollback` | `via_policy_rollback` | `backend/app/api/routers/intelligence.py:505` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/via/policies/{version_key}/advance-rollout` | `via_policy_advance_rollout` | `backend/app/api/routers/intelligence.py:516` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/student/batches` | `student_create_batch` | `backend/app/api/routers/intelligence.py:673` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/student/cards/{qr_id}/revoke` | `student_revoke_card` | `backend/app/api/routers/intelligence.py:727` | high-impact mutation (clear); missing/unclear: audit |
| P0 | POST | `/student/cards/{qr_id}/reissue` | `student_reissue_card` | `backend/app/api/routers/intelligence.py:737` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/viltrox/scan-now` | `viltrox_scan_now` | `backend/app/api/routers/intelligence.py:753` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | POST | `/viltrox/reset-official-roster` | `viltrox_reset_official_roster` | `backend/app/api/routers/intelligence.py:769` | high-impact mutation (clear, reset); missing/unclear: audit, confirm/reason |
| P0 | POST | `/system/cache/clear` | `system_cache_clear` | `backend/app/api/routers/intelligence.py:790` | high-impact mutation (clear); missing/unclear: audit, confirm/reason |
| P0 | DELETE | `/kols/{kol_id}` | `delete_kol` | `backend/app/api/routers/kol_ops.py:546` | high-impact mutation (delete); missing/unclear: audit |
| P0 | POST | `/{platform}/webhook` | `ingest_platform_webhook` | `backend/app/api/routers/platform_ingest.py:34` | business mutation with no obvious permission dependency in router function |
| P0 | POST | `/api/creator-public/click` | `creator_public_click` | `backend/app/api/routers/student_identity.py:91` | business mutation with no obvious permission dependency in router function |
| P0 | POST | `/api/student/signup` | `student_signup` | `backend/app/api/routers/student_identity.py:146` | business mutation with no obvious permission dependency in router function |
| P0 | POST | `/api/student/pass/check-in` | `student_pass_check_in` | `backend/app/api/routers/student_identity.py:179` | business mutation with no obvious permission dependency in router function |
| P0 | POST | `/runtime/cache/{tier}/clear` | `clear_cache_tier` | `backend/app/api/routers/system_admin.py:209` | high-impact mutation (clear); missing/unclear: confirm/reason |
| P0 | POST | `/system/providers/{provider}/probe` | `probe_provider` | `backend/app/api/routers/system_admin.py:242` | high-impact mutation (api-key, api_key); missing/unclear: audit, confirm/reason |
| P0 | POST | `/users/{user_id}/clear-flag` | `clear_flag` | `backend/app/api/routers/system_admin.py:543` | high-impact mutation (clear); missing/unclear: confirm/reason |
| P0 | POST | `/staff/accept-invite` | `accept_staff_invite` | `backend/app/api/routers/system_admin.py:629` | business mutation with no obvious permission dependency in router function |
| P0 | DELETE | `/staff/{staff_id}` | `delete_staff_member` | `backend/app/api/routers/system_admin.py:738` | high-impact mutation (delete); missing/unclear: confirm/reason |
| P0 | POST | `/staff/api-tokens` | `create_token` | `backend/app/api/routers/system_admin.py:794` | high-impact mutation (api-key, api_key); missing/unclear: confirm/reason |
| P0 | DELETE | `/staff/api-tokens/{token_id}` | `revoke_token` | `backend/app/api/routers/system_admin.py:812` | high-impact mutation (api-key, api_key, delete); missing/unclear: rollback/soft-delete, confirm/reason |
| P0 | PATCH | `/sessions/{session_key}/persona` | `patch_via_persona` | `backend/app/api/routers/via.py:76` | business mutation with no obvious permission dependency in router function |
| P0 | POST | `/sessions/{session_key}/memory/refresh` | `refresh_via_memory` | `backend/app/api/routers/via.py:90` | business mutation with no obvious permission dependency in router function |
| P0 | POST | `/sessions/{session_key}/events` | `post_via_event` | `backend/app/api/routers/via.py:99` | business mutation with no obvious permission dependency in router function |
| P0 | POST | `/sessions/{session_key}/respond` | `respond_via_session` | `backend/app/api/routers/via.py:117` | business mutation with no obvious permission dependency in router function |
| P0 | POST | `/sessions/{session_key}/reward-traces` | `post_via_reward_trace` | `backend/app/api/routers/via.py:135` | business mutation with no obvious permission dependency in router function |
| ... | ... | ... | ... | ... | 另有 31 条,见 CSV 全量矩阵 |

## P1 Findings - 下一批验证/治理
| Risk | Method | Endpoint | Function | File | Issue |
|---|---:|---|---|---|---|
| P1 | POST | `/scan-account` | `api_scan` | `backend/app/api/routers/account_scanner.py:15` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/scan-matrix` | `api_matrix` | `backend/app/api/routers/account_scanner.py:45` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/social-accounts/{account_id}/verify` | `admin_verify_social` | `backend/app/api/routers/admin.py:571` | mutation has permission hint but no obvious audit hint in router/function body |
| P1 | POST | `/api/admin/users/upsert` | `admin_upsert_user_account` | `backend/app/api/routers/admin.py:617` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/users/{uid}/approve` | `admin_approve_user` | `backend/app/api/routers/admin.py:629` | operational mutation (approve) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/users/{uid}/reject` | `admin_reject_user` | `backend/app/api/routers/admin.py:640` | operational mutation (reject) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/creator-public/shop-heroes` | `admin_upsert_creator_shop_hero` | `backend/app/api/routers/admin.py:873` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/submissions/manual` | `manual_add_submission` | `backend/app/api/routers/admin.py:997` | mutation has permission hint but no obvious audit hint in router/function body |
| P1 | POST | `/api/verify/register` | `register_verification` | `backend/app/api/routers/admin.py:1031` | mutation has permission hint but no obvious audit hint in router/function body |
| P1 | POST | `/api/admin/verifications/{ver_id}/approve` | `approve_verification` | `backend/app/api/routers/admin.py:1065` | operational mutation (approve) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/verifications/{ver_id}/reject` | `reject_verification` | `backend/app/api/routers/admin.py:1080` | operational mutation (reject) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/reject/{submission_id}` | `manual_reject` | `backend/app/api/routers/admin.py:1293` | operational mutation (reject) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/rewards` | `admin_create_reward` | `backend/app/api/routers/admin.py:1381` | mutation has permission hint but no obvious audit hint in router/function body |
| P1 | PATCH | `/api/admin/rewards/{rid}` | `admin_update_reward` | `backend/app/api/routers/admin.py:1412` | mutation has permission hint but no obvious audit hint in router/function body |
| P1 | POST | `/api/admin/rewards/{rid}/publish` | `admin_publish_reward` | `backend/app/api/routers/admin.py:1424` | mutation has permission hint but no obvious audit hint in router/function body |
| P1 | POST | `/api/admin/rewards/{rid}/archive` | `admin_archive_reward` | `backend/app/api/routers/admin.py:1435` | mutation has permission hint but no obvious audit hint in router/function body |
| P1 | POST | `/api/admin/users/batch-grant-points` | `admin_batch_grant_points` | `backend/app/api/routers/admin.py:1516` | high-impact mutation (grant_points) has basic guard hints; dynamic verification required |
| P1 | POST | `/api/admin/users/grant-points-by-rule` | `admin_grant_points_by_rule` | `backend/app/api/routers/admin.py:1547` | high-impact mutation (grant_points) has basic guard hints; dynamic verification required |
| P1 | POST | `/api/admin/redemptions/{rid}/update` | `admin_update_redemption` | `backend/app/api/routers/admin.py:1862` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/redemptions/{rid}/approve` | `admin_approve_redemption` | `backend/app/api/routers/admin.py:1933` | operational mutation (approve, sync) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/redemptions/{rid}/pack` | `admin_pack_redemption` | `backend/app/api/routers/admin.py:1939` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/redemptions/{rid}/ship` | `admin_ship_redemption` | `backend/app/api/routers/admin.py:1945` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/redemptions/{rid}/deliver` | `admin_deliver_redemption` | `backend/app/api/routers/admin.py:1951` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/api/admin/redemptions/{rid}/reject` | `admin_reject_redemption` | `backend/app/api/routers/admin.py:1957` | operational mutation (reject, sync) with permission hint but no obvious audit hint |
| P1 | POST | `/change-password` | `change_password` | `backend/app/api/routers/auth.py:368` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/analyze-brand` | `analyze_brand` | `backend/app/api/routers/brand_analysis.py:108` | operational mutation (analyze, sync) with permission hint but no obvious audit hint |
| P1 | POST | `/orders/{order_id}/attribute` | `override_attribution` | `backend/app/api/routers/commerce.py:103` | high-impact mutation (reassign) has basic guard hints; dynamic verification required |
| P1 | POST | `/social-accounts` | `add_social_account` | `backend/app/api/routers/creator.py:246` | operational mutation (claim, sync) with permission hint but no obvious audit hint |
| P1 | POST | `/addresses` | `creator_add_address` | `backend/app/api/routers/creator.py:313` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/redeem` | `creator_redeem` | `backend/app/api/routers/creator.py:324` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | PATCH | `/profile` | `update_profile` | `backend/app/api/routers/creator.py:407` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | PATCH | `/addresses/{address_id}/default` | `set_default_address` | `backend/app/api/routers/creator.py:459` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/evidence-pack` | `build_pack` | `backend/app/api/routers/deepsight.py:55` | operational mutation (analyze, generate, sync) with permission hint but no obvious audit hint |
| P1 | POST | `/diagnose` | `diagnose` | `backend/app/api/routers/deepsight.py:69` | operational mutation (analyze, sync) with permission hint but no obvious audit hint |
| P1 | POST | `/scan-official-matrix` | `scan_official_matrix` | `backend/app/api/routers/deepsight.py:75` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/monitor` | `intel_monitor` | `backend/app/api/routers/intelligence.py:252` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/compare` | `intel_compare` | `backend/app/api/routers/intelligence.py:285` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/learn/url` | `learn_from_url` | `backend/app/api/routers/intelligence.py:349` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/via/evaluate-now` | `via_evaluate_now` | `backend/app/api/routers/intelligence.py:412` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/student/schools` | `student_save_school` | `backend/app/api/routers/intelligence.py:648` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/intelligence/market/gaps/generate` | `generate_market_gaps` | `backend/app/api/routers/intelligence_admin.py:59` | operational mutation (generate, sync) with permission hint but no obvious audit hint |
| P1 | POST | `/intelligence/brand/insights/generate` | `generate_brand_insights` | `backend/app/api/routers/intelligence_admin.py:91` | operational mutation (generate, sync) with permission hint but no obvious audit hint |
| P1 | PUT | `/intelligence/brand/voice` | `update_brand_voice` | `backend/app/api/routers/intelligence_admin.py:101` | mutation has permission hint but no obvious audit hint in router/function body |
| P1 | POST | `/search/platform` | `search_kol_platform` | `backend/app/api/routers/kol_ops.py:146` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/tools/analyze-url` | `analyze_kol_url_tool` | `backend/app/api/routers/kol_ops.py:180` | operational mutation (analyze, sync) with permission hint but no obvious audit hint |
| P1 | PATCH | `/candidates/{candidate_id}` | `update_candidate` | `backend/app/api/routers/kol_ops.py:261` | mutation has permission hint but no obvious audit hint in router/function body |
| P1 | POST | `/candidates/{candidate_id}/promote` | `promote_candidate` | `backend/app/api/routers/kol_ops.py:292` | operational mutation (promote) with permission hint but no obvious audit hint |
| P1 | POST | `/kols/{kol_id}/scan-account` | `scan_kol_account_endpoint` | `backend/app/api/routers/kol_ops.py:395` | operational mutation (sync) with permission hint but no obvious audit hint |
| P1 | POST | `/kols/{kol_id}/analyze-account` | `analyze_kol_account_endpoint` | `backend/app/api/routers/kol_ops.py:414` | operational mutation (analyze, sync) with permission hint but no obvious audit hint |
| P1 | POST | `/kols` | `create_kol` | `backend/app/api/routers/kol_ops.py:466` | operational mutation (promote) with permission hint but no obvious audit hint |
| ... | ... | ... | ... | ... | 另有 121 条,见 CSV 全量矩阵 |

## Top Files By Mutation Count
| File | Mutation endpoints |
|---|---:|
| `backend/app/api/routers/admin.py` | 37 |
| `backend/app/api/routers/system_admin.py` | 28 |
| `backend/app/api/routers/intelligence.py` | 20 |
| `backend/app/api/routers/vkpi_operations.py` | 18 |
| `backend/app/api/routers/vkpi_industry_automation.py` | 13 |
| `backend/app/api/routers/vkpi_kol_links.py` | 12 |
| `backend/app/api/routers/kol_ops.py` | 11 |
| `backend/app/api/routers/vkpi_evidence_assets.py` | 11 |
| `backend/app/api/routers/commerce.py` | 10 |
| `backend/app/api/routers/vkpi_projects.py` | 10 |
| `backend/app/api/routers/vkpi_attribution_metrics.py` | 9 |
| `backend/app/api/routers/auth.py` | 8 |
| `backend/app/api/routers/kol_ops_content.py` | 8 |
| `backend/app/api/routers/creator.py` | 7 |
| `backend/app/api/routers/verify.py` | 7 |
| `backend/app/api/routers/vkpi_settings.py` | 7 |
| `backend/app/api/routers/via.py` | 6 |
| `backend/app/api/routers/vkpi_data_quality.py` | 6 |
| `backend/app/api/routers/vkpi_product_analysis.py` | 6 |
| `backend/app/api/routers/activities.py` | 5 |

## Interpretation
- 这次扫描的核心结论不是“功能没做”,而是写接口治理层次不均: 权限多数存在,但确认、审计、回滚需要按端点动态验。
- 静态 P0 不能直接等同于漏洞; 它是下一步真实 HTTP QA 的优先队列。
- P3 收口不应继续盲目加功能,应把关键写接口治理成“权限明确、变更可追溯、误操作可解释/可回滚”。

## Recommended Next Verification Set
1. Settings/Firewall 写接口: 普通员工拒绝、admin 通过、audit 表有记录。
2. KOL/项目删除、认领、重分配接口: 验证软删/状态回滚或至少写入审计原因。
3. 导入/上传/抓取触发接口: 验证权限、文件限制、预算 gate、错误清理。
4. 输出 `mutation-safety-dynamic-qa.md`,再决定哪些按钮加确认弹窗。
