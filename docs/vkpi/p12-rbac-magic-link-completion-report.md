# V-KPI P12 RBAC and Magic Link Completion Report

## Status

P12 is complete for the current v5.3.1 execution scope.

Implemented packages:

```text
P12-0 RBAC/Magic Link boundary document
P12-1 read-only RBAC status CLI
P12-3 read-only RBAC status API
P12-4 read-only Settings page status card
```

P12-2 magic-link hardening was not implemented because the P12-1 snapshot found no immediate invite-token gaps in the current local database.

## Commits

```text
7cf699b docs(vkpi): design P12 RBAC and magic link boundary
86c622d feat(vkpi): add RBAC status snapshot CLI
bbbf4fe feat(vkpi): expose RBAC status API
4be9cca feat(vkpi): show RBAC status in settings
```

## Current Snapshot

Command:

```bash
.venv/bin/python scripts/p12_rbac_status.py
```

Observed local state:

```text
scenario=p12_rbac_status
provider_calls=false
write_db=false
staff.total=2
staff.active=2
staff.accepted=2
staff.pending_invite=0
staff.suspended=0
staff.owners=2
staff.active_owners=2
access.active_can_read_vkpi=2
access.active_can_write_vkpi=2
access.active_can_admin_vkpi=2
access.active_can_manage_members=2
invite_tokens.total=0
invite_tokens.active=0
invite_tokens.expired_unused=0
invite_tokens.used=0
gaps=none
```

## API

Read-only endpoint:

```text
GET /api/admin/vkpi/access/rbac-status
```

Gate:

```python
require_tab("vkpi", "admin")
```

Response invariants:

```text
provider_calls=false
write_db=false
scenario=p12_rbac_status
```

## Frontend

Settings page now includes a read-only V-KPI permissions status card.

Files:

```text
frontend/src/services/vkpi.ui-api.ts
frontend/src/components/vkpi/pages/SettingsPage.tsx
frontend/src/components/vkpi/pages/settings/SettingsAdminCards.tsx
```

The card shows:

```text
active staff
active owner count
active V-KPI read/write/admin counts
staff_invite token status
write_db/provider_calls invariants
RBAC gaps
```

It does not add invite, permission-update, suspend, or token-write controls.

## Verification

```text
.venv/bin/python -m py_compile backend/app/services/vkpi/rbac_status.py backend/app/api/routers/vkpi_access.py backend/app/main.py scripts/p12_rbac_status.py passed
.venv/bin/python scripts/p12_rbac_status.py passed
router import smoke passed
backend/app/main.py import smoke passed
npm run build passed
git diff --check passed
```

Frontend build output only included the existing Vite chunk-size warning.

## Boundary

P12 preserved the existing identity and permission system:

```text
users
staff
email_tokens
admin_audit_log
require_tab
require_system_permission
```

P12 did not create:

```text
vkpi_staff
parallel invite token table
parallel auth flow
task allocator
new permission hierarchy
```

## Remaining Optional Work

Only if a later snapshot shows gaps:

```text
expire older unused staff_invite tokens when resending invite
surface pending/expired invite details per staff row
add audit log for staff_invite accept events
add owner-only bulk invite cleanup
```
