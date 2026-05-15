# P4.2A Write Endpoint Mechanical Inventory

Generated at: 2026-05-15T04:00:06.534924+00:00
Repository: `/Users/bibiboer/Documents/V-KPI——marketing`

> This inventory is the P4.2A mechanical extraction output. It does not include risk judgement. Risk level, confirmation, audit effectiveness, and rollback capability are handled in P4.2B.

## Outputs

- JSONL: `docs/audits/p4_2a_write_endpoint_inventory.jsonl`
- CSV: `docs/audits/p4_2a_write_endpoint_inventory.csv`

## Sanity Check

- AST write endpoint rows: `296`
- rg-like `@router.(post|put|patch|delete)` count: `292`
- Delta: `4`
- APIRouter alias decorators captured by AST: `4`
- Delta is expected when routes use APIRouter aliases such as `public_router` or `webhook_router`; P4.2A keeps these rows because they are still write endpoints.

## Method Distribution

| Method | Count |
|---|---:|
| DELETE | 16 |
| PATCH | 25 |
| POST | 251 |
| PUT | 4 |

## Mechanical Flags

- `file_is_vkpi OR path_is_vkpi`: `146`
- `has_permission_dep_grep=false`: `26`
- `has_audit_grep=false`: `287`
- `has_permission_dep_grep=false AND has_audit_grep=false`: `26`

## Top Routers By Write Endpoint Count

| Router | Count |
|---|---:|
| `admin.py` | 37 |
| `system_admin.py` | 28 |
| `intelligence.py` | 20 |
| `vkpi_operations.py` | 18 |
| `vkpi_industry_automation.py` | 13 |
| `vkpi_kol_links.py` | 12 |
| `kol_ops.py` | 11 |
| `vkpi_evidence_assets.py` | 11 |
| `commerce.py` | 10 |
| `vkpi_projects.py` | 10 |
| `vkpi_attribution_metrics.py` | 9 |
| `auth.py` | 8 |
| `kol_ops_content.py` | 8 |
| `creator.py` | 7 |
| `verify.py` | 7 |
| `vkpi_settings.py` | 7 |
| `via.py` | 6 |
| `vkpi_data_quality.py` | 6 |
| `vkpi_product_analysis.py` | 6 |
| `activities.py` | 5 |
| `vkpi_kol_pool.py` | 5 |
| `vkpi_reconciliation.py` | 5 |
| `deepsight.py` | 4 |
| `vkpi_costs.py` | 4 |
| `audit.py` | 3 |
| `intelligence_admin.py` | 3 |
| `student_identity.py` | 3 |
| `vkpi.py` | 3 |
| `vkpi_comment_intelligence.py` | 3 |
| `vkpi_firewall.py` | 3 |

## Launch Scope Note

- Launch-before focus for P4.2B-1 should be selected from this inventory, not from route count alone.
- First-pass candidates remain `vkpi_settings.py`, `vkpi_industry_automation.py`, `vkpi_evidence_assets.py`, and `vkpi_operations.py`, subject to P4.2B review.
- Launch-after routers should be audited after the launch-critical P0/P1 slice is understood.

## P4.2B Suggested Filters

```bash
jq -r 'select(.file_is_vkpi == true or .path_is_vkpi == true)' docs/audits/p4_2a_write_endpoint_inventory.jsonl
jq -r 'select((.file_is_vkpi == true or .path_is_vkpi == true) and .has_permission_dep_grep == false and .has_audit_grep == false)' docs/audits/p4_2a_write_endpoint_inventory.jsonl
```
