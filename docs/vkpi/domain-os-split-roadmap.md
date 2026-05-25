# V-KPI Domain OS Split Roadmap

Status: active

This roadmap tracks the post-backup architecture migration. The rule is strict: new source files should stay below 800 lines, and legacy files above 800 lines must be split in bounded batches.

## Current Position

| Track | Status | Notes |
|---|---|---|
| D0 Backup | Done | Full project/runtime/history backup completed before migration. |
| D1 Domain skeleton | Done | Backend/frontend domain/platform/shared folders and line guard added. |
| D2 API bus split | Mostly done | Product-facing pages now use domain API facades; only Repair and the temporary dashboard aggregator still touch `vkpi.ui-api.ts`. |
| D3 Frontend big-file split | In progress | `DiscoverPage.tsx` is being split first because it is core product UI and safer than Repair. |

## D2 Remaining Work

| Batch | Target | Action | Acceptance |
|---|---|---|---|
| D2-H | Dashboard data adapter | Split `fetchVkpiDashboardData` out of `vkpi.ui-api.ts` into dashboard/project/link/cost/report builders. | `dashboard-api.ts` no longer imports `vkpi.ui-api.ts`; build passes. |
| D2-I | Repair API freeze split | Move Repair imports to `repair-api.ts` only after Repair v0 remains frozen. | `RepairCenterPage.tsx` no longer imports `vkpi.ui-api.ts`; no new Repair scope added. |
| D2-J | Legacy bus shrink | Stop adding exports to `vkpi.ui-api.ts`; leave compatibility only until all callers are gone. | `rg "vkpi.ui-api" frontend/src` has only comments or zero matches. |

## D3 Frontend Split Order

| Batch | File | What Moves Out | Target Files | Acceptance |
|---|---|---|---|---|
| D3-A | `DiscoverPage.tsx` | Queue / focus / decision banners | `DiscoverQueuePanels.tsx` | Build passes; child file < 800 lines. |
| D3-B | `DiscoverPage.tsx` | Recommendation list | `DiscoverRecommendationPanel.tsx` | Build passes; child file < 800 lines. |
| D3-C | `DiscoverPage.tsx` | Search results / candidate cards / search progress | `DiscoverSearchPanel.tsx` | Build passes; child file < 800 lines. |
| D3-D | `DiscoverPage.tsx` | Profile cards by section | `DiscoverProfilePanel.tsx`, then smaller section files if needed | `DiscoverPage.tsx` drops under ~1800 lines. |
| D3-E | `DiscoverPage.tsx` | Pure model builders and mappers | `discoverModel.ts`, `discoverEvidence.ts` | UI file keeps orchestration only. |
| D3-F | `DashboardPremium.tsx` | Hero / intelligence layer / KPI grid / map / trend panels | `dashboard-premium/*` | No child > 800 lines; build passes. |
| D3-G | `IntelligenceCenterPage.tsx` | Card list / evidence drawer wiring / agent panels | `intelligence-center/*` | Main page < 800 lines. |
| D3-H | `ProjectDetailView.tsx` | Header / timeline / evidence / cost / attribution sections | `projects/detail/*` | Main detail page < 800 lines. |
| D3-I | CSS split | Split huge CSS by page section | colocated CSS modules or section CSS files | No CSS file > 800 lines unless intentionally generated. |
| D3-J | Repair UI | Freeze first, then split view into tabs/sections | `repair-center/*` | No new Repair scope; page import surface clean. |

## D4 Backend Split Order

| Batch | File | Split Direction | Acceptance |
|---|---|---|---|
| D4-A | `repair_repository.py` | Keep frozen; split persistence/readiness/query helpers only after UI split. | No behavior change; tests pass. |
| D4-B | `vkpi/memory.py` | Facts, retrieval, write policy, summaries. | Each module < 800 lines. |
| D4-C | `channels.py` | Matrix, posts, comments, delta, media. | Existing channel tests pass. |
| D4-D | `media_cache.py` | Policy, R2, local cache, resolver. | Media cache tests pass. |
| D4-E | `kol_pool.py` | Selector, refresh tier, import, promotion, summaries. | KOL pool tests pass. |
| D4-F | `daily_sync.py` | Sync command, selectors, provider adapters, reports. | No legacy full KOL refresh accidentally re-enabled. |
| D4-G | platform legacy | `admin.py`, `session_service.py`, `via_learning.py` only after V-KPI core splits. | App import and targeted tests pass. |

## D5 Domain Realignment

| Domain | Owns | Should Not Own |
|---|---|---|
| `domains/marketing-intelligence` | market signals, competitor trends, Google/Reddit/RSS/Gemini/LLM summaries | UI-only cards or Repair workflow |
| `domains/kol-discovery` | KOL search, KOL Pool, refresh tiers, evidence, candidate decisions | project cost / attribution |
| `domains/campaign-workflow` | projects, tasks, stages, shipments, owner workflow | global dashboard display |
| `domains/commerce-attribution` | links, Shopify/Amazon/manual attribution, cost ledger | KOL profile scoring |
| `domains/operations` | sync guard, settings, budgets, audits, data quality | business recommendation logic |

## Execution Rule

Each batch must end with one of:

- `npm --prefix frontend run build`
- targeted backend tests
- `PYTHONPATH=backend .venv/bin/python scripts/check_line_guard.py --no-tests`
- a short status table showing current file sizes and remaining violations

No provider runs, LLM/Gemini runs, DB writes, migrations, or deployment are part of this split unless explicitly approved for that batch.
