# V-KPI P11-2 TaskCenter SSE Adapter

## Scope

P11-2 adds optional SSE acceleration to the existing TaskCenter.

Polling remains the source of truth.

Files:

```text
frontend/src/services/vkpi.ui-api.ts
frontend/src/components/tasks/TaskCenter.tsx
```

## Runtime Flow

```text
1. TaskCenter calls GET /api/admin/vkpi/tasks/realtime-status.
2. If SSE is unavailable, TaskCenter keeps existing polling only.
3. If SSE is available, TaskCenter opens EventSource for active task ids.
4. SSE status events trigger a polling refresh.
5. Terminal SSE events trigger a final polling refresh and close that EventSource.
6. SSE errors close that EventSource and polling continues.
```

## Auth Boundary

P11-2 does not put bearer tokens in EventSource URLs.

It uses the existing same-origin stream route:

```text
/api/audit/stream/{task_id}
```

If a deployment requires bearer-only auth and does not support cookie/same-origin stream access, SSE silently disables itself through error fallback and polling remains active.

## Invariants

```text
provider_calls=false
write_db=false
polling_fallback_required=true
```

## Acceptance

```text
1. npm run build passes.
2. Existing listTasks polling remains unchanged.
3. TaskCenter waitForTask callbacks still fire from the polling refresh path.
4. Active tasks have at most one EventSource per task id.
5. EventSource closes on terminal event, error, logout, or unmount.
```
