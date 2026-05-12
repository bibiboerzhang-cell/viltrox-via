# P3.9P - Data Quality Action Density Fix

Date: 2026-05-13
Branch: codex/vkpi-cleanup-d7
Commit before: bfbac37

## Problem

The Data Quality page rendered five row-level action buttons at once:

- assign
- rerun
- evidence
- resolve
- ignore

This kept every action technically wired, but the table became visually overloaded and hard to operate.

## Change

- Keep `已处理` as the primary visible row action.
- Move secondary actions (`指派`, `重检`, `补证据`, `忽略`) into a compact `更多` details menu.
- Keep every action wired to `actOnIssue(...)`; no action was removed or made decorative.
- Add static P3 QA guard so this compact pattern cannot silently regress.

## Files

- `frontend/src/components/vkpi/pages/DataQualityPage.tsx`
- `frontend/src/components/vkpi/VkpiDashboard.css`
- `scripts/smoke_vkpi_p3_2_full_qa_audit.py`

## Verification

- `./scripts/run_smoke.sh smoke_vkpi_p3_2_full_qa_audit.py smoke_vkpi_p3_1h_button_actions.py` PASS=2 / FAIL=0
- `./scripts/run_smoke.sh smoke_vkpi_data_quality.py` PASS=1 / FAIL=0
- `npm run build` PASS
- Browser QA opened `/#dataQuality` and confirmed rows now show `已处理` + `更多`; opening `更多` exposes `指派`, `重检`, `补证据`, `忽略`.

## Boundary

This round only fixes the Data Quality page action density. It does not change issue generation rules, cleanup historical smoke data, or redefine the data quality scoring model.
