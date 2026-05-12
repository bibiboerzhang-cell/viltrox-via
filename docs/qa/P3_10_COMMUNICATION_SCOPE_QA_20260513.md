# P3.10 Communication History Scope QA - 2026-05-13

## Scope

This round verifies the existing V-KPI project communication history path instead of creating a new schema.

Existing implementation confirmed:

- `vkpi_messages` and `vkpi_message_attachments` already exist.
- `POST /api/marketing/projects/{project_id}/messages` writes a project communication record through the frontend-facing alias.
- `GET /api/marketing/projects/{project_id}` returns `messages`.
- `workflow.add_project_message()` enforces `scope.assert_project_access(project_id, staff, write=True)`.
- `workflow.project_detail()` enforces `scope.assert_project_access(project_id, staff)`.
- Successful message capture writes `vkpi_business_audit_logs.action_type='message_capture'`.

## Verification Added

Added:

- `scripts/smoke_vkpi_p3_10_communication_scope.py`

The smoke covers:

- assigned employee can add a communication record through `/api/marketing`.
- assigned employee can read the record back from project detail.
- unrelated employee gets `403` on project detail.
- unrelated employee gets `403` on project message write.
- owner/admin can read the communication record.
- `message_capture` audit exists for the created message.
- seeded KOL/project/message/audit/user/staff rows are cleaned up.

## Commands

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
PYTHONPATH=backend .venv/bin/python -m py_compile scripts/smoke_vkpi_p3_10_communication_scope.py
./scripts/run_smoke.sh smoke_vkpi_p3_10_communication_scope.py
```

## Result

```text
PASS=1 / FAIL=0 / TOTAL=1
VKPI_P3_10_COMMUNICATION_SCOPE_SMOKE_OK
```

## Current Risk Notes

- This closes the backend communication-history scope path, not the full CRM UX.
- The frontend already has message entry and evidence upload controls in `ProjectEvidenceForms.tsx`, but browser QA should still test real click paths after the next bundle rebuild.
- `/health` currently showed `client_matches_server=false`; that is a frontend build/cache consistency issue and should be treated separately from this backend communication scope smoke.

## P3 Position

P3.10A is closed as backend/API validation.

Remaining P3.10 work, if needed:

- P3.10B browser QA for the project detail communication form.
- P3.10C optional UX polish for communication categories and follow-up reminders.
