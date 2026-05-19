# V-KPI P12-1 RBAC Status CLI

## Scope

P12-1 adds a read-only RBAC and magic-link invite status snapshot.

It reads:

```text
staff
users
email_tokens
admin_audit_log
backend/app/core/permissions.py
```

It does not write any database row.

## CLI

```bash
python3 scripts/p12_rbac_status.py
python3 scripts/p12_rbac_status.py --json
python3 scripts/p12_rbac_status.py --include-staff --json
python3 scripts/p12_rbac_status.py --json-out /tmp/p12_rbac_status.json --md-out /tmp/p12_rbac_status.md
```

Default output is Markdown.

## Snapshot Fields

```json
{
  "scenario": "p12_rbac_status",
  "provider_calls": false,
  "write_db": false,
  "staff": {
    "total": 0,
    "active": 0,
    "accepted": 0,
    "pending_invite": 0,
    "suspended": 0,
    "owners": 0,
    "active_owners": 0,
    "missing_email": 0,
    "domain_verified": 0
  },
  "roles": {},
  "active_vkpi_permissions": {},
  "active_system_members_permissions": {},
  "effective_access": {
    "active_can_read_vkpi": 0,
    "active_can_write_vkpi": 0,
    "active_can_admin_vkpi": 0,
    "active_can_manage_members": 0
  },
  "invite_tokens": {
    "total": 0,
    "active": 0,
    "expired_unused": 0,
    "used": 0
  },
  "gaps": []
}
```

## Gap Rules

```text
staff_table_empty
no_active_owner_staff
no_active_vkpi_reader
no_active_vkpi_admin
pending_staff_invites
expired_unused_staff_invite_tokens
staff_rows_missing_user_email
```

These are diagnostic flags only. P12-1 does not auto-fix them.

## Acceptance

```text
1. CLI runs without writing DB rows.
2. provider_calls=false.
3. write_db=false.
4. Snapshot uses existing staff/users/email_tokens tables.
5. No vkpi_staff table or migration is introduced.
6. Per-staff row output is opt-in with --include-staff.
```
