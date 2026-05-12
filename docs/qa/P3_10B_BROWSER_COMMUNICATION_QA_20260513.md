# P3.10B Browser QA - Project Communication Evidence

Date: 2026-05-13
Repo: `/Users/bibiboer/Documents/V-KPI——marketing`
Round: P3.10B

## Scope

Browser-level QA for the real project communication workflow:

1. Login with a seeded admin/staff user through the actual root login screen.
2. Open `项目跟进`.
3. Open a real project detail drawer from the project table.
4. Add a message record from the UI.
5. Upload a local evidence file from the UI.
6. Verify the project detail API returns the saved message and uploaded evidence URL.

## Seed

- Marker: `vkpi-p310b-browser-1778618470`
- Project ID: `3026`
- Project name: `P3.10B Browser QA Project vkpi-p310b-browser-1778618470`
- KOL: `P3.10B Browser QA vkpi-p310b-browser-1778618470`

## Browser QA Result

- Login through `/`: PASS
- `/login` direct route: returns `Public surface is disabled on this instance`; not used as the active app login path.
- Project page visible after login: PASS
- Seed project appears in project list: PASS
- Project detail opens from explicit `详情` row action: PASS
- Message form visible in detail drawer: PASS
- Message body saved from UI: PASS
- Evidence file upload saved from UI: PASS
- API readback `/api/marketing/projects/3026`: PASS
- Saved evidence URL returned after explicit-button rerun: `/uploads/vkpi_evidence/20260512/13dcc002a0d74d78b3ccb399dd7fadff-p310b-message-evidence.txt`
- API 4xx/5xx during tested path: none

## UI Fix Applied

`ProjectTable` now exposes a real `详情` button in each row. Previously opening the project detail depended on clicking the table row, which worked but was not obvious enough and could be mistaken for a dead UI path. Row action buttons now have explicit spacing so `详情` and `删除` do not visually merge.

Touched file:

- `frontend/src/components/vkpi/tables/ProjectTable.tsx`
- `frontend/src/components/vkpi/VkpiDashboard.css`

## Notes

The browser console showed two 404s and one `ERR_NAME_NOT_RESOLVED` during the QA run. They did not affect the tested API path. The DNS error came from the synthetic seeded avatar/evidence domain used for QA, not from a production API call.

## Cleanup

The seeded user, staff, KOL, project, message, content asset rows and the two `p310b-message-evidence.txt` upload files were removed after verification.

## Acceptance

- Real UI operation writes a `vkpi_messages` row.
- Real UI upload produces a persisted `/uploads/vkpi_evidence/...` URL.
- Project detail API returns the saved message and evidence URL.
- The project list has an explicit details action instead of relying only on row click.
