# P3.9I Project Browser Flow QA

Date: 2026-05-13
Workspace: /Users/bibiboer/Documents/V-KPI——marketing
Backup: /Users/bibiboer/Documents/V-KPI-backups/vkpi-before-p39i-project-browser-flow-20260513-022656.tar.gz

## Scope

This round runs a controlled browser QA path for project creation and project operation buttons. It uses a temporary marker project and removes the test records after validation.

Test marker:

- `P39I_QA_1778610619577`

## Browser Flow Results

| Step | Action | Browser result | Verdict |
|---|---|---|---|
| 1 | Open `#projects` | Page loaded, project form visible. Initial state had `0 条真实项目`. | PASS |
| 2 | Fill project name, choose existing KOL, fill product SKU/name | Form accepted fields. | PASS |
| 3 | Click `创建项目` | Page showed `项目已创建。`; project appeared in list; no visible 500. | PASS |
| 4 | Click `完成当前步，进入：已联系` | Page showed `已推进到：已联系`; project stage updated; no visible 500. | PASS |
| 5 | Fill shipping and promotion cost, click `计入快递 / 推广费` | Page showed `快递费 / 推广费已计入项目...`; project list showed `成本 $69`; no visible 500. | PASS |
| 6 | Inspect attachment controls | Source code confirms real `input type=file` controls and upload handler path. Browser automation could not inject file into `input[type=file]` with current tool surface. | PARTIAL |
| 7 | Cleanup test data | Removed project and child records by marker. `remaining_projects = 0`. | PASS |

## Cleanup Evidence

Deleted records after the browser test:

- `vkpi_metric_sources`: 11
- `vkpi_project_stage_events`: 2
- `vkpi_cost_ledger`: 2
- `vkpi_kol_claims`: 1
- `vkpi_projects`: 1
- remaining marker projects: 0

## Findings

1. Project creation is a real browser-to-backend flow, not a fake button.
2. Stage transition is real and updates the visible project state.
3. Shipping/promotion cost registration is real and updates visible cost totals.
4. Attachment controls are wired in code as real file inputs and submit through `uploadMarketingEvidenceFile`, but the browser automation tool could not complete a file-selection interaction in this pass.
5. The current project creation flow still lacks the desired decision UX: KOL selection is a basic dropdown, product selection is text input, and there is no searchable product/KOL picker yet.

## Remaining Issues For Project UX

These are product gaps, not endpoint absence:

- Product field should become a searchable single/multi-select product picker.
- KOL field should become searchable and should open KOL detail/metrics before selection.
- Attachment upload needs a browser-level manual/automated QA pass with real file selection.
- Created project should expose a direct detail drawer with evidence links immediately after creation.
- Need permission/ownership filtering before team usage: current browser path still assumes global visibility.

## Acceptance Status

P3.9I controlled project browser flow: PASS for create, stage, and cost buttons; PARTIAL for upload due browser automation file-selection limitation.
