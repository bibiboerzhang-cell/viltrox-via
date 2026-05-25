# V-KPI Domain OS Architecture

Status: active migration plan

## Why This Exists

The current V-KPI codebase has outgrown a page-first and flat-service layout. The immediate signs are:

- `backend/app/services/vkpi` has more than 150 top-level Python files.
- `frontend/src/components/vkpi/pages` holds page shells and business logic together.
- Several source files are above 2,000 lines, with the largest frontend page above 7,000 lines.
- New intelligence, market, Gemini, LLM, attribution, KOL, and project logic would keep landing in the same overloaded files without a stronger boundary.

The new target is a domain-first architecture. Pages become composition shells. Business behavior moves into domains. Cross-cutting infrastructure moves into platform modules.

## Hard Rules

1. Business source files must stay at or below 800 lines.
2. New feature code must land in a domain or platform module, not in legacy flat folders.
3. Page components compose domain components. They do not own fetching, scoring, classification, provider calls, or workflow state machines.
4. Provider calls never go directly to dashboards. They flow through provider/platform gateways, then domain ingest/classification, then intelligence cards.
5. Every intelligence output must have evidence and a feedback path.
6. Repair Center v0 is frozen as a governance tool. It must not become the main product execution path.

## Target Frontend Shape

```text
frontend/src/domains/
  dashboard/
  intelligence/
  market/
  kol/
  projects/
  products/
  attribution/
  settings/
  repair/

frontend/src/platform/
  auth/
  rbac/
  api/
  telemetry/
  runtime/

frontend/src/shared/
  ui/
  charts/
  formatters/
  evidence/
  types/
```

## Target Backend Shape

```text
backend/app/domains/
  dashboard/
  intelligence/
  market/
  kol/
  projects/
  products/
  attribution/
  settings/
  repair/

backend/app/platform/
  db/
  auth/
  rbac/
  audit/
  jobs/
  providers/
  budget/
  media/
  observability/

backend/app/shared/
  contracts/
  errors/
  time/
  typing/
```

## Intelligence Flow

```text
external provider
  -> platform/providers
  -> domain ingest
  -> domain classifier/scorer
  -> intelligence card
  -> evidence drawer
  -> accept / reject / snooze / done
  -> feedback / outcome / calibration
```

Dashboard displays selected cards and metrics. It does not own the intelligence logic.

## Migration Strategy

Use a strangler pattern:

1. Add the new domain skeleton without moving behavior.
2. Add adapters so old imports keep working.
3. Move one domain at a time.
4. Verify each batch with build, targeted tests, line guard, and route/API checks.
5. Delete old shells only after the replacement path is proven.

## Batch Plan

| Batch | Scope | Acceptance |
|---:|---|---|
| D0 | Line guard, domain rules, current violation report | Guard runs and reports debt |
| D1 | Domain/platform/shared skeletons | Empty packages can be imported |
| D2 | Split frontend API clients | Build passes; old calls still work |
| D3 | Intelligence domain | Cards, evidence, feedback have a stable home |
| D4 | Market domain | Google/RSS/Reddit/competitor signal code moves out of flat services |
| D5 | Dashboard domain | Dashboard page becomes a composition shell |
| D6 | Dashboard styles | Large CSS splits into domain style modules |
| D7 | Repair domain freeze | Repair v0 is isolated and not extended |
| D8 | Repair backend split | Repository breaks into focused modules |
| D9 | KOL domain | Search, pool, profile, product fit move into KOL domain |
| D10 | Projects domain | Board/detail/workflow/evidence move together |
| D11 | Products domain | Product analysis and competitor analysis settle under products |
| D12 | Attribution domain | Links, sales attribution, and costs consolidate |
| D13 | Settings domain | Account, permissions, budget, and rules consolidate |
| D14 | Backend flat-service shrink | `services/vkpi` no longer acts as the primary feature namespace |
| D15 | Platform extraction | Providers, budget, audit, media, and jobs become platform modules |
| D16 | Intelligence provider readiness | LLM/Gemini/provider calls use the new gateway path |
| D17 | Backup parity check | Compare current behavior against the full backup baseline |
| D18 | Legacy cleanup | Remove empty adapters and dead shells |
| D19 | Domain OS acceptance report | Publish final exceptions and ownership map |

## Completion Definition

- No non-exempt business source file is above 800 lines.
- New work enters domain/platform/shared modules only.
- `DashboardPremium.tsx`, `RepairCenterPage.tsx`, `DiscoverPage.tsx`, and `vkpi.ui-api.ts` are no longer logic monoliths.
- The intelligence layer has a single card/evidence/feedback contract.
- Market, KOL, projects, products, attribution, and settings each have an obvious owner folder.
- The full backup `vkpi-full-20260524T200059Z` remains the comparison baseline for parity checks.
