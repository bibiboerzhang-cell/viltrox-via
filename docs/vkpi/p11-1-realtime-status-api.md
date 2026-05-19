# V-KPI P11-1 Realtime Status API

## Scope

P11-1 adds a read-only realtime readiness endpoint for the existing task queue and SSE support.

Endpoint:

```text
GET /api/admin/vkpi/tasks/realtime-status
```

Permission:

```python
require_tab("vkpi", "read")
```

## Response

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
  "runtime": {},
  "gaps": []
}
```

## Behavior

The endpoint checks:

```text
sse_starlette.sse import availability
request.app.state.job_queue presence
job_queue.subscribe_task_events availability
job_queue.runtime_stats if available
```

It does not open an SSE stream. It only reports whether the current runtime can support one.

## Gap Names

```text
sse_starlette_missing
job_queue_missing
task_event_subscription_missing
```

## Acceptance

```text
1. Endpoint is mounted before /api/admin/vkpi/tasks/{task_id}.
2. provider_calls=false.
3. write_db=false.
4. No database writes occur.
5. Existing polling task endpoints are unchanged.
6. Frontend TaskCenter is unchanged in P11-1.
```
