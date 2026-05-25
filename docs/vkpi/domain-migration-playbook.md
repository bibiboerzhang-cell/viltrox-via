# V-KPI Domain Migration Playbook

Use this checklist for every Domain OS migration slice. A migration slice is not complete until this checklist is satisfied or an explicit exception is documented.

## Required Checklist

- [ ] The owning domain is named before code is moved. If ownership cannot be named, do not split yet.
- [ ] Frontend domain code lands under `frontend/src/domains/<domain>/`.
- [ ] Backend domain code lands under `backend/app/domains/<domain>/`.
- [ ] Existing legacy entrypoints remain as compatibility wrappers when needed.
- [ ] New business behavior does not land in oversized legacy files.
- [ ] Domain API calls leave the domain through `api.ts`, not direct scattered imports from legacy UI code.
- [ ] Cross-domain or infrastructure calls use platform/shared adapters or documented compatibility wrappers.
- [ ] `index.ts` or `__init__.py` exposes only the public domain facade.
- [ ] Domain README states ownership, upstream dependencies, outputs, and out-of-scope items.
- [ ] Source files stay at or below 800 lines.
- [ ] The slice is reviewable without unrelated Repair, provider, migration, or UI-cleanup noise.
- [ ] Frontend build passes when frontend files change.
- [ ] Backend compile/tests pass when backend files change.
- [ ] The progress report separates file-size debt reduction from real Domain business migration.

## Recommended Frontend Shape

```text
frontend/src/domains/<domain>/
  README.md
  api.ts
  components/
  hooks.ts
  index.ts
  types.ts
```

Keep page shells thin. Move reusable behavior into domain hooks/components. During migration, existing pages may consume the new domain facade until routing moves into the domain.

## Recommended Backend Shape

```text
backend/app/domains/<domain>/
  README.md
  __init__.py
  service.py
```

Routers should call the domain facade for business behavior. Legacy services can remain as implementation providers during the first slice, but the route boundary should point at the domain.

## First PoC: Data Quality

The first PoC intentionally avoids external dependencies:

- no provider calls
- no sync trigger
- no LLM/Gemini/Apify
- no migration execution
- read-only data-quality summary through a domain facade

Files added by the PoC:

```text
backend/app/domains/data_quality/
frontend/src/domains/data-quality/
```

Completion criteria:

- Data Quality page uses the frontend domain facade for the summary cards.
- `vkpi_data_quality` GET route calls the backend domain facade.
- Legacy write/remediation actions remain outside the PoC.
- Build and backend verification pass.
