# V-KPI P12 RBAC and Magic Link Boundary

## Scope

P12 hardens the existing access-control and invite flow for V-KPI. It does not create a second identity system.

Current canonical identity tables:

```text
users
staff
email_tokens
admin_audit_log
```

Current permission code:

```text
backend/app/core/permissions.py
backend/app/api/dependencies/perms.py
backend/app/services/system/staff.py
backend/app/api/routers/system_admin.py
```

Current staff endpoints:

```text
GET    /api/admin/staff
POST   /api/admin/staff/invite
POST   /api/admin/staff/accept-invite
PATCH  /api/admin/staff/{staff_id}
POST   /api/admin/staff/{staff_id}/permissions
POST   /api/admin/staff/{staff_id}/suspend
POST   /api/admin/staff/{staff_id}/reactivate
POST   /api/admin/staff/{staff_id}/resend-invite
GET    /api/admin/staff/roles
GET    /api/admin/staff/permission-matrix
GET    /api/admin/staff/audit-log
```

## Non-Goals

P12 does not:

```text
create vkpi_staff
replace users or staff
replace require_tab or require_system_permission
change existing auth cookie behavior
change V-KPI recommendation scoring
write new project/staff assignment logic
add frontend UI in the first package
add task allocator behavior
```

If V-KPI later needs module-specific staff metadata, use an additive extension table that references `staff.id`. Do not duplicate staff names, emails, roles, or permissions into a V-KPI-specific staff master table.

## Existing RBAC Model

The current permission layer is matrix-based:

```text
none < read < write < admin
```

Owner accounts bypass tab and system checks. Non-owner staff use `staff.permissions_json` normalized through `default_permissions_for_role()` and `normalize_permissions()`.

V-KPI routes should continue to use:

```python
require_tab("vkpi", "read")
require_tab("vkpi", "write")
require_tab("vkpi", "admin")
```

System-member operations should continue to use:

```python
require_system_permission("system.members", "write")
```

## Existing Magic Link Flow

Staff invite already uses the shared token table:

```text
email_tokens.type = 'staff_invite'
```

Current flow:

```text
1. staff.invite() creates or links a users row.
2. staff.invite() creates a staff row.
3. staff.invite() creates a staff_invite token.
4. staff.accept_invite() validates token, password, expiry, and used_at.
5. staff.accept_invite() updates user password, verifies email, activates staff, marks token used.
```

P12 must harden this flow instead of creating another invite-token table.

## P12-1 Read-Only RBAC Snapshot

First code package should be read-only.

It should produce:

```json
{
  "scenario": "p12_rbac_status",
  "write_db": false,
  "provider_calls": false,
  "staff": {
    "total": 0,
    "active": 0,
    "accepted": 0,
    "pending_invite": 0,
    "suspended": 0,
    "owners": 0
  },
  "roles": {},
  "vkpi_permissions": {},
  "system_members_permissions": {},
  "invite_tokens": {
    "active": 0,
    "expired_unused": 0,
    "used": 0
  },
  "gaps": []
}
```

Inputs:

```text
staff
users
email_tokens
admin_audit_log
backend/app/core/permissions.py constants
```

No rows should be inserted or updated.

## P12-2 Magic Link Hardening

Second code package should only patch gaps found by P12-1.

Allowed fixes:

```text
expire older unused staff_invite tokens when resending invite
surface pending/expired invite counts
record invite/resend/accept actions in admin audit where missing
add CLI inspection for staff_invite token health
```

Disallowed fixes:

```text
parallel invite table
parallel staff table
frontend-only invite state
weakening OWNER_ONLY_SYSTEM_KEYS
granting system.members write to non-owner accounts
```

## P12-3 V-KPI Permission Readiness

Third package may expose a V-KPI-only readiness endpoint:

```text
GET /api/admin/vkpi/access/rbac-status
```

It should require:

```python
require_tab("vkpi", "admin")
```

The endpoint must remain read-only and return the P12-1 snapshot.

## Acceptance

P12-0 acceptance:

```text
1. Document confirms staff/users/email_tokens are canonical.
2. Document explicitly rejects vkpi_staff.
3. Document maps existing RBAC and staff invite files/endpoints.
4. Document defines P12-1 as read-only snapshot.
5. Document leaves UI, task allocation, and new auth architecture out of scope.
```

P12 code acceptance:

```text
1. No new staff master table.
2. No provider calls.
3. No LLM calls.
4. No writes in P12-1.
5. V-KPI access status is explainable from current staff rows and permission matrix.
6. Magic link tokens stay in email_tokens.
```

## Package Plan

```text
P12-0 docs boundary
P12-1 read-only RBAC snapshot CLI/service
P12-2 magic link invite health hardening if snapshot finds gaps
P12-3 read-only V-KPI access API
P12-4 frontend admin surface only after API is stable
```
