# V-KPI P3.18 Feedback Admin

P3.17 gave every internal tester a feedback entry point. P3.18 closes the loop for managers:

- list feedback in System Settings
- filter by status
- move feedback through `triaged`, `in_progress`, `resolved`, and `closed`
- verify the same backend HTTP path with a smoke test

## Files

- `frontend/src/components/vkpi/pages/settings/SettingsFeedbackPanel.tsx`
- `frontend/src/components/vkpi/pages/SettingsPage.tsx`
- `frontend/src/services/vkpi.ui-api.ts`
- `scripts/smoke_vkpi_p3_18_feedback_admin.py`

## Acceptance

- Settings page shows `内测反馈管理` for manager/admin users.
- `GET /api/admin/vkpi/feedback` loads real DB feedback rows.
- `PATCH /api/admin/vkpi/feedback/{uid}` updates status.
- `scripts/smoke_vkpi_p3_18_feedback_admin.py` prints `VKPI_P3_18_FEEDBACK_ADMIN_SMOKE_OK`.
