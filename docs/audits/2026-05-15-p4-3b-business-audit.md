# P4.3B Business Mutation Audit Coverage

- Generated: 2026-05-15
- Marker: `p4_3b_1778823945`
- Backup before change: `/Users/bibiboer/Documents/V-KPI-backups/before-p4-3b-business-audit-20260515-133351.tar.gz`
- Scope: Service-level `vkpi_business_audit_logs` coverage for five P4.2B-1 P0 business mutations.
- Method: FastAPI TestClient against real routes, auth dependencies, services, and DB writes. Isolated marker data was cleaned after validation.
- Non-goal: UI confirmation and cron safety; those are P4.3A and P4.3C.

## Summary

- Checks: `5`
- PASS: `5`
- FAIL: `0`

## Result Matrix

| Endpoint | Action Type | Result | HTTP | DB Evidence | Audit Evidence | Cleanup | Notes |
|---|---|---|---:|---|---|---|---|
| POST /automation/models/{model_version}/activate | automation_model_activate | PASS | 200 | marker_model_active=True | vkpi_business_audit_logs=1 ids=[13483] | model registry restored; audit row deleted | Executed with marker model only; previous registry state restored. |
| POST /budget-pools | budget_pool_create | PASS | 200 | pool_id=3 total=300 | vkpi_business_audit_logs=1 ids=[13484] | marker pool/allocation/audit rows deleted | Financial object creation now writes centralized business audit. |
| POST /budget-pools/{pool_id}/allocate | budget_pool_allocate | PASS | 200 | allocations=1 amount=100 | vkpi_business_audit_logs=1 ids=[13485] | marker allocation/pool/audit rows deleted | Financial allocation now writes centralized business audit. |
| POST /staff/{staff_id}/offboard/initiate | staff_offboarding_initiate | PASS | 200 | run_id=2 status=pending | vkpi_business_audit_logs=1 ids=[13486] | marker offboarding/audit rows deleted | Executed against isolated marker staff only. |
| POST /offboarding/{run_id}/execute | staff_offboarding_execute | PASS | 200 | run_status=completed result_json_len=271 | vkpi_business_audit_logs=1 ids=[13487] | marker offboarding/audit rows deleted | Executed only on isolated marker staff; no real staff affected. |

## Acceptance

- `automation_model_activate` records previous and new model metadata.
- `budget_pool_create` and `budget_pool_allocate` record financial mutation metadata.
- `staff_offboarding_initiate` and `staff_offboarding_execute` record target staff, new owner, action list, and result summary.

## Next

- P4.3C: restrict/confirm cron run endpoints and verify audit for allowed cron jobs only.
