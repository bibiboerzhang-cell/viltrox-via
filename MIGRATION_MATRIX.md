# Viltrox 2.0 Migration Matrix

## Scope

This file tracks what is currently the same, what is intentionally different, and what is still pending between:

- legacy runtime: `/Users/jianbozhang/Downloads/viltrox-app-test/viltrox-test`
- new runtime: `/Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0`

## Already the same

### Backend business surface

- Router inventory is currently preserved 1:1 in `backend/app/api/routers`.
- `auth`
- `creator`
- `verify`
- `student_identity`
- `uploads`
- `via`
- `admin`
- `audit`
- `jobs`
- `leaderboard`
- `media`
- `intelligence`
- `ops`

### Core account and identity capability

- Email register/login/verify/reset flow is still available through copied backend routers.
- Creator code generation remains part of the user/account stack.
- Social binding and verification routes are preserved.
- Student QR signup and pass endpoints are preserved.
- Upload/audit/video analysis entrypoints are preserved.

### Legacy parity references

- Frozen copies of old `index`, `admin`, `login`, and `student signup` pages are included as visual/behavior references.

## Intentionally different in 2.0

### Project layout

- Old version mixes legacy HTML, backend, large uploads, virtualenv, and tools in one runtime tree.
- New version isolates runtime code into:
  - `backend/`
  - `frontend/`
  - `migrations/`
  - `scripts/`
  - legacy HTML references

### Frontend architecture

- Old frontend is a lighter Vite React preview surface.
- New frontend is route-first and service-layered:
  - `src/app/*`
  - `src/routes/public`
  - `src/routes/admin`
  - `src/routes/account`
  - `src/routes/rewards`
  - `src/services/*`
  - `src/store/*`
  - `src/types/*`

### Runtime entrypoints

- New runtime serves:
  - `/`, `/admin`, `/account`, `/redeem` from the new React build
  - `/login`, `/student-signup` from the same React app shell and route bundle

### Session isolation

- 2.0 uses a separate frontend token key: `via_token_v2`.
- This avoids clobbering local auth state from the legacy runtime.

### Packaging and handoff

- 2.0 adds a lightweight share flow that excludes:
  - `frontend/node_modules`
  - `uploads`
  - `venv`
  - `_tools`
  - local DB files
- Current share archive stays well below the 500 MB target.

### Deployment intent

- 2.0 is prepared for separated runtime roles:
  - `public-web`
  - `admin-web`
  - `worker`
- Launch scripts already exist for each role.

## Not migrated yet

### Frontend parity still to finish

- `login` is still served from a frozen legacy reference page.
- `student-signup` is still served from a frozen legacy reference page.
- `admin` is functional, but not yet 1:1 with every legacy admin section.
- KPI coverage is not yet fully mapped into dedicated 2.0 dashboards.

### Backend infrastructure hardening still to do

- Postgres is not yet the active default runtime.
- Redis is not yet the mandatory queue/cache/status backbone.
- Event-driven SSE has not yet replaced transitional polling behavior everywhere.
- Video factory is not yet decomposed into a dedicated ingest/decode/transcode/analyze pipeline.
- Via 2.0 reasoning stack is not yet separated into truth/knowledge/reasoning/control layers.

### Future-facing capability still pending

- Higher-throughput worker orchestration
- richer KPI aggregation/read models
- 3D Via embodiment hooks
- more advanced agent reasoning and controlled memory/procedural learning
- high-traffic production deployment profile

## Immediate migration order

1. Reconnect auth and login UX onto native 2.0 pages.
2. Reconnect social binding and verification views into the 2.0 account surface.
3. Reconnect student signup/pass into native 2.0 flows.
4. Upgrade upload -> job -> analysis -> result into a clearer video factory pipeline.
5. Expand Via 2.0 from current shared backend into its dedicated control/reasoning shape.
6. Finish admin KPI parity and command views.

## Guardrails

- Legacy runtime stays untouched.
- 2.0 should not share the old auth token key.
- 2.0 should not depend on legacy large assets to remain shareable.
- Behavior parity comes before visual reinvention.
- External event ingresses must ship with schema-level idempotency in the migration itself.
- Historical event replay must stay opt-in behind explicit cutoff/include-history controls.
