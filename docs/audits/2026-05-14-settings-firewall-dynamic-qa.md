# Settings / Firewall Dynamic QA - 2026-05-14

范围: P4 Step 22 只验证 V-KPI 系统设置与防火墙写接口。目标是把 Step 21 静态审计里的 Settings/Firewall 风险项转成真实 HTTP + DB 结论。

## Tested Paths

| Path | Method | Expected | Result |
|---|---:|---|---|
| `/api/admin/vkpi/settings/firewall/feature-flags` | POST | employee `vkpi:write` must be rejected | PASS: 403 |
| `/api/admin/vkpi/settings/firewall/feature-flags` | POST | admin writes feature flag to DB | PASS |
| `/api/admin/vkpi/settings/firewall/platform/{platform}` | POST | admin writes platform crawl settings to DB | PASS |
| `/api/admin/vkpi/settings/firewall/budget/{budget_key}` | POST | admin writes budget settings to DB | PASS |
| `/api/admin/vkpi/settings/feature-flags` | PATCH | employee `vkpi:write` must be rejected | PASS: 403 |
| `/api/admin/vkpi/settings/feature-flags` | PATCH | admin legacy settings endpoint still writes DB | PASS |

## Audit Verification

| Audit Layer | Verified For | Result |
|---|---|---|
| `vkpi_settings_change_logs` | feature flag | PASS |
| `vkpi_settings_change_logs` | platform crawl | PASS |
| `vkpi_settings_change_logs` | budget setting | PASS |
| `vkpi_business_audit_logs` via `@audit_action` | firewall feature flag | PASS |
| `vkpi_business_audit_logs` via `@audit_action` | firewall platform update | PASS |
| `vkpi_business_audit_logs` via `@audit_action` | firewall budget update | PASS |

## Findings

- `vkpi_firewall.py` dynamic result is stronger than the static Step 21 report: admin gate, service audit, and business audit are all functioning for tested write endpoints.
- `vkpi_settings.py` legacy settings endpoints correctly enforce `require_tab("vkpi", "admin")` and write service-level settings audit through `platform_crawl_settings.py`.
- Legacy settings endpoints do not use `@audit_action`; for this module that is acceptable if `vkpi_settings_change_logs` remains the canonical settings audit table.
- Test data is marker-scoped and cleaned up after each run.

## Commands

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile scripts/smoke_vkpi_p4_22_settings_firewall_dynamic_qa.py
./scripts/run_smoke.sh smoke_vkpi_p4_22_settings_firewall_dynamic_qa.py
./scripts/run_smoke.sh smoke_vkpi_firewall_router.py
PYTHONPATH=backend .venv/bin/pytest tests/ -q
```

## Conclusion

Settings/Firewall 写接口不是当前 P0 阻塞项。下一步应转向 Step 21 矩阵里仍未动态验证的 KOL/项目删除、认领、重分配类接口。
