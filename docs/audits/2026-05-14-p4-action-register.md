# P4 Action Register - 2026-05-14

This register converts the P4 audits into executable next steps. Each step is one module, with matching validation only.

## Progress Indicator

- Audit package and boundary system: done
- Static backend mutation matrix: done
- Settings/firewall dynamic QA: done
- KOL/project lifecycle dynamic QA: done
- P4 audit checkpoint: 24/24 reached
- Remaining: targeted remediation and internal-usage hardening

## Next 20 Executable Steps

| # | Module | Action | Validation |
|---:|---|---|---|
| 1 | Runtime health | Add QA preflight checklist requiring `/health.build.client_matches_server=true` before browser QA | curl `/health`; report only |
| 2 | Dirty worktree | Classify current 29 changes by feature area before any commit | `git status --short`, no code test |
| 3 | Media UX | Verify full-list, open original, playback fallback, and single-post drawer on real account | media smoke + browser QA |
| 4 | Media UX | If missing, patch only media component behavior; no backend aggregation | `npm run build`, media smoke |
| 5 | Daily Top100 | Run scheduled-source diagnostic against current DB rows and trigger path | Daily Top100 diagnostic smoke |
| 6 | Daily Top100 | Patch trigger/empty-state wording only if source is absent or ambiguous | Daily Top100 smoke + browser check |
| 7 | DataAnalysis lineage | Ensure KPI cards show source tooltip consistently | `npm run build`, visual/browser check |
| 8 | DataAnalysis beta controls | Mark local-only controls as `Beta/local view` instead of pretending full backend truth | `npm run build` |
| 9 | DataQuality UI | Group dense buttons into sections and disable unsupported actions with reason tooltip | `npm run build`, browser QA |
| 10 | Export/report endpoints | Dynamic QA for PDF/CSV/week report endpoints with real auth and no fake-success | export/report smoke |
| 11 | Admin/system high-risk | Audit top admin DELETE/block/grant endpoints dynamically | targeted HTTP smoke |
| 12 | Commerce high-risk | Audit approve/process/backfill/override endpoints dynamically | targeted HTTP smoke |
| 13 | VIA/intelligence high-risk | Audit apply/promote/rollback/cache-clear endpoints dynamically | targeted HTTP smoke |
| 14 | Outreach v1 | Start only after host integration contract is accepted | component tests + host integration smoke |
| 15 | Cost Dashboard Phase A | Audit existing ledgers/tables before creating dashboard | audit report only |
| 16 | Cost Dashboard Phase B | Implement only after Phase A decision | cost smoke + pytest |
| 17 | Monitoring | Add production health/runbook and alert thresholds | health smoke + docs review |
| 18 | Backup/restore | Define and test DB + attachment restore path | restore drill report |
| 19 | Internal test pack | Prepare team handoff package with known limitations | package scan + release notes |
| 20 | Two-week observation | Collect real staff feedback and usage logs; do not treat feedback as immediate rewrite | feedback report |

## Immediate Recommended Next Step

Do Step 1 and Step 2 next. Reason: Step23 proved stale process can invalidate QA; dirty worktree remains 29 changes. Fixing process visibility and change classification prevents false bug reports and bad commits.

## Non-Goals For Next Step

- Do not rebuild Socialinsider-level analytics now.
- Do not start Outreach or Cost Dashboard implementation until their audit/contract steps are accepted.
- Do not mix Media UX patching with Daily Top100 or DataQuality UI cleanup.
