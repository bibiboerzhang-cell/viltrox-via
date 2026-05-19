# V-KPI P11 SSE Task Stream v0

## Scope

P11 is optional and last. It should improve task-status latency without replacing the existing polling path.

Current backend already has an SSE router:

```text
backend/app/api/routers/sse.py
GET /api/audit/stream/{task_id}
GET /api/audit/task/{task_id}/status
```

Current frontend task center uses polling:

```text
frontend/src/components/tasks/TaskCenter.tsx
frontend/src/services/vkpi.ui-api.ts
```

P11 should bridge these two carefully.

## Non-Goals

P11 does not:

```text
replace TaskCenter polling
change async task schema
change queue backend
add provider calls
write database rows
stream recommendation result payloads beyond task status
weaken task permission checks
```

## Design Principle

SSE is an acceleration layer, not the source of truth.

Source of truth remains:

```text
GET /api/marketing/tasks?status=...
GET /api/audit/task/{task_id}/status
```

SSE may update a visible active task earlier, but polling must continue as fallback.

## Current Gap

The current SSE route is audit-oriented:

```text
/api/audit/stream/{task_id}
```

The V-KPI TaskCenter currently lists marketing tasks through:

```text
/api/marketing/tasks
```

Directly wiring EventSource to the audit route creates two risks:

```text
1. Browser EventSource cannot set Authorization headers.
2. The frontend task center would depend on an audit namespace instead of a V-KPI/marketing task namespace.
```

P11 should not hide these risks behind a frontend-only patch.

## Package Plan

```text
P11-0 design SSE boundary
P11-1 read-only realtime readiness API
P11-2 frontend TaskCenter SSE adapter with polling fallback
P11-3 smoke docs and completion report
```

## P11-1 Readiness API

Add a read-only endpoint:

```text
GET /api/admin/vkpi/tasks/realtime-status
```

It should report:

```json
{
  "scenario": "p11_realtime_status",
  "provider_calls": false,
  "write_db": false,
  "sse_available": true,
  "job_queue_present": true,
  "task_event_subscription_available": true,
  "polling_fallback_required": true,
  "stream_routes": [
    "/api/audit/stream/{task_id}",
    "/api/audit/task/{task_id}/status"
  ],
  "gaps": []
}
```

This endpoint should require:

```python
require_tab("vkpi", "read")
```

## P11-2 Frontend Adapter

Add frontend support without removing polling:

```text
1. TaskCenter keeps polling every 3s/30s exactly as today.
2. For active tasks only, open EventSource if readiness says SSE is available.
3. On any SSE error, close EventSource and continue polling.
4. Terminal SSE events trigger one final polling refresh.
5. Do not pass bearer tokens in URL unless a backend auth-safe route is added.
```

Because EventSource cannot send Authorization headers, P11-2 should prefer same-origin cookie-based access. If deployed auth requires bearer-only access, P11-2 must stay disabled and rely on polling.

## Acceptance

```text
1. Existing polling still works if SSE is unavailable.
2. No provider calls.
3. No DB writes.
4. Backend readiness reports current SSE capability.
5. Frontend build passes.
6. No task result data is exposed without the existing task-id route check.
7. Terminal task events still trigger watcher callbacks exactly once.
```

## Stop Conditions

Do not proceed to frontend SSE wiring if:

```text
sse_starlette is missing
job_queue lacks subscribe_task_events
auth requires bearer-only tokens for task status
TaskCenter polling is already unstable
```
