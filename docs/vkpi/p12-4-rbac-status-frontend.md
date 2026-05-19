# V-KPI P12-4 RBAC Status Frontend

## Scope

P12-4 adds a read-only RBAC status card to the existing V-KPI Settings page.

It calls:

```text
GET /api/admin/vkpi/access/rbac-status
```

It does not call staff invite or permission update endpoints.

## Placement

The card is rendered in:

```text
frontend/src/components/vkpi/pages/SettingsPage.tsx
```

The API helper is:

```text
frontend/src/services/vkpi.ui-api.ts
```

The display component is:

```text
frontend/src/components/vkpi/pages/settings/SettingsAdminCards.tsx
```

## Display

The card shows:

```text
active staff
active owner count
active V-KPI read/write/admin counts
active staff_invite token count
expired unused staff_invite token count
write_db/provider_calls invariants
gaps
```

## Failure Behavior

If the RBAC endpoint is not available or the current staff user lacks `vkpi admin`, the card shows a local error and does not block other Settings page data.

## Acceptance

```text
1. Frontend build passes.
2. No new write button is added.
3. Existing staff invite and permission-update controls remain unchanged.
4. RBAC card renders from the P12-3 read-only endpoint.
5. No provider calls are introduced.
```
