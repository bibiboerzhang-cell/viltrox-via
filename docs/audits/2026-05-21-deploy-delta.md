# V-KPI Deploy Delta · 2026-05-21

- Generated at: `2026-05-20T20:12:52Z`
- Current live build: `524527c3`
- Local target build: `9cb1786`
- Branch: `codex/settings-five-module-simplify`
- Purpose: freeze the exact commit range before deploying the current local UI/data surface to `viltroxtest.com`.

## Commit Delta

```text
9cb1786 style(ui): apply glass shell theme to vkpi
a8e59b1 fix(ui): embed premium dashboard with real official data
5637f16 fix(ui): route manager control to premium dashboard
c40a4b7 fix(ui): align premium dashboard kol distribution labels
c799a94 test(vkpi): cover kol pool country distribution
c3b7756 feat(vkpi): backfill kol pool country values
f0f660a feat(vkpi): surface kol country distribution
0fd0d4f feat(ui): default vkpi shell to premium dashboard
bb5d5b4 fix(ui): tighten glass dashboard responsive layout
0bf1085 feat(ui): expose dashboard premium hash page
4462464 feat(ui): connect dashboard premium data adapters
442d545 fix(vkpi): allow larger r2 migration batches
e2f1292 refactor(ui): extract dashboard premium skeleton
76c21a1 feat(ui): wire glass demo hash route
81aa451 feat(ui): add glass future component primitives
ed53f86 feat(ui): add scoped glass future css
```

## Risk Notes

- This range is mostly UI/dashboard work plus the R2 migration cap fix.
- If browser QA fails after deploy, first isolate between:
  - glass shell styling: `9cb1786`, `ed53f86`, `81aa451`
  - premium dashboard data/route: `a8e59b1`, `5637f16`, `0fd0d4f`, `0bf1085`, `4462464`
  - country distribution: `c40a4b7`, `c799a94`, `c3b7756`, `f0f660a`
  - R2 migration cap: `442d545`

## Search Memory Card Decision

KOL history memory card should be an enrichment layer, not owned by a single search path.

Decision:
- Keep `DiscoverPage` / `listMarketingKols` and `/kol/search/natural` as separate search sources.
- Normalize search results to stable KOL identifiers when possible.
- After search results arrive, enrich visible rows through a single memory-card endpoint, e.g. `/api/admin/vkpi/kol-pool/{id}/memory-card` or equivalent.
- If a platform candidate has no `kol_pool_id`, show candidate state and defer memory-card enrichment until the candidate is promoted or matched.

Acceptance:
- Historical KOL results keep memory card data whether they came from legacy search or natural search.
- Fallback from natural search to legacy search does not make cooperation history, contact status, or competitor risk disappear.
- Candidate-only rows remain clearly labeled as candidate / not deep-scanned / no historical match.

## 11-Dimension Honesty Rule

`dimensions_11_json` must preserve confidence per dimension.

Rule:
- Do not fill missing dimensions with plausible default scores.
- Each dimension should carry enough metadata to distinguish measured, inferred, and missing values.
- UI should gray out or hide dimensions with confidence `0`.

Minimum shape:

```json
{
  "score": 72,
  "confidence": 0.8,
  "source": "legacy_cooperation|platform_metrics|competitor_relation|rule_inference|missing",
  "evidence": []
}
```
