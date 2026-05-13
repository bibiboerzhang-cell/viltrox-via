# P3.17 Team Feedback Loop

## Scope

P3.17 adds the internal-test feedback loop needed after team handoff. It is intentionally not a new analytics feature.

## Delivered

- `POST /api/admin/vkpi/feedback`: staff submit page/button/data issues from the UI.
- `GET /api/admin/vkpi/feedback`: admin triage list.
- `PATCH /api/admin/vkpi/feedback/{uid}`: admin status update.
- `vkpi_team_feedback`: persistent feedback table.
- Global floating feedback widget in the V-KPI dashboard shell.
- Smoke: `scripts/smoke_vkpi_p3_17_feedback_loop.py`.

## Acceptance

- A logged-in V-KPI user can submit a feedback item from any page.
- Admin can list and triage the feedback item.
- Audit logs include feedback create and status update events.
- The widget does not block existing page usage.

## P3 Progress Note

This is the observation loop before real user feedback. It does not attempt Socialinsider parity. P3 closure still means team-usable V-KPI: scoped access, real media paths, project loop, settings, monitoring, backup, and a feedback channel.
