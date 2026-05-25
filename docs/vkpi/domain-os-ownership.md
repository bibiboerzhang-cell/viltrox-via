# V-KPI Domain Ownership

Status: active rulebook

## Ownership Map

| Domain | Owns | Does Not Own |
|---|---|---|
| `dashboard` | Management overview, metric presentation, selected intelligence summaries | Provider calls, raw market ingestion, recommendation scoring |
| `intelligence` | Cards, evidence, actions, feedback, agent outputs, prediction display | Raw crawlers, low-level provider auth, project implementation details |
| `market` | Google/RSS/Reddit signals, competitor mentions, trend classification | UI-only dashboard layout, KOL profile editing |
| `kol` | KOL pool, discovery, profile, product fit, competitor relation | Campaign workflow and sales attribution |
| `projects` | Project board, stages, tasks, evidence, owner workflow | Global KOL search and provider crawling |
| `products` | SKU catalog, product analysis, product-vs-competitor analysis | Project task state and sales ledger |
| `attribution` | Short links, Shopify/Amazon attribution, costs, ROI | Product specs and market crawler logic |
| `settings` | Account/profile, permissions, budget rules, platform settings | Product analytics and intelligence generation |
| `repair` | Frozen v0 repair preview/governance surface | Product roadmap, market intelligence, automated live repairs |
| `platform` | DB, auth, RBAC, audit, jobs, providers, budget, media, observability | Business scoring and page-level product decisions |
| `shared` | UI primitives, formatters, cross-domain types, evidence primitives | Feature-specific behavior |

## File Placement Rules

1. A file must have one owner domain.
2. If two domains need the same utility, put only the generic utility in `shared`; keep domain behavior in the domain.
3. If a file talks to external services, budget, queues, auth, media, or audit, it belongs in `platform` unless it is a domain-level adapter over a platform interface.
4. New frontend pages should live inside a domain and export a thin route component.
5. New backend routers may keep FastAPI registration under `backend/app/api/routers`, but the behavior must be delegated to `backend/app/domains/<domain>`.
6. New scripts must either be domain-scoped in naming or call a domain module directly.

## 800-Line Rule

The 800-line line guard is a hard stop for new business source.

Allowed exceptions:

- Migrations.
- Generated contract output.
- Static seed data.
- Legacy files listed in the current migration debt report.

Not allowed:

- Adding one more feature to an already oversized page.
- Compressing code formatting to reduce line count.
- Hiding unrelated behavior in a `utils` file.

## Import Direction

Preferred direction:

```text
page/route
  -> domain facade
  -> domain service
  -> platform/shared
```

Forbidden direction:

```text
platform/shared
  -> domain
```

Cross-domain calls should go through a small facade or contract. Do not import another domain's internal module directly.

## Batch Rule

Each migration batch must state:

- Source files being migrated.
- New domain files being created.
- Adapter kept for backwards compatibility.
- Tests/build commands run.
- Remaining legacy files and why.

No batch is accepted without a line-guard report.
