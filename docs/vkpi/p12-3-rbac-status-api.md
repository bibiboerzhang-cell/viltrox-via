# V-KPI P12-3 RBAC Status API

## Scope

P12-3 exposes the P12-1 RBAC snapshot through a read-only V-KPI admin endpoint.

Endpoint:

```text
GET /api/admin/vkpi/access/rbac-status
```

Optional query:

```text
include_staff=true
```

## Permission Gate

The endpoint requires:

```python
require_tab("vkpi", "admin")
```

It does not grant or mutate permissions. Staff membership and invite writes remain under the existing system-admin endpoints.

## Behavior

The API calls:

```python
app.services.vkpi.rbac_status.build_rbac_status()
```

Response invariants:

```text
provider_calls=false
write_db=false
scenario=p12_rbac_status
```

## Non-Goals

P12-3 does not:

```text
add frontend UI
create vkpi_staff
create migrations
send invite emails
accept invite tokens
change permission defaults
```

## Acceptance

```text
1. Router is registered in backend/app/main.py.
2. Endpoint is gated by vkpi admin permission.
3. API returns the same snapshot shape as scripts/p12_rbac_status.py.
4. No database writes occur.
5. No provider calls occur.
```
