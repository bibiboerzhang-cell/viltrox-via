# V-KPI Domain OS Data Quality PoC

Date: 2026-05-24 PT

## Purpose

This is the first real Domain OS migration slice. It is intentionally small and read-only so the migration process is tested without provider, sync, LLM, Gemini, Apify, or migration side effects.

## Scope

Included:

- Data Quality summary API facade.
- Data Quality summary hook and summary-card component.
- Legacy page consumes the new frontend domain facade for the summary cards.
- Existing GET route calls the backend domain facade.
- Legacy write/remediation actions remain unchanged.

Excluded:

- Sync trigger.
- Provider calls.
- LLM/Gemini/Apify.
- DB migration execution.
- Brand-signal review migration.
- Data-quality issue write/remediation migration.

## Files

Frontend domain files:

```text
frontend/src/domains/data-quality/README.md
frontend/src/domains/data-quality/api.ts
frontend/src/domains/data-quality/components/DataQualitySummaryCards.tsx
frontend/src/domains/data-quality/hooks.ts
frontend/src/domains/data-quality/index.ts
frontend/src/domains/data-quality/types.ts
```

Backend domain files:

```text
backend/app/domains/data_quality/README.md
backend/app/domains/data_quality/__init__.py
backend/app/domains/data_quality/service.py
```

Compatibility and integration:

```text
backend/app/api/routers/vkpi_data_quality.py
frontend/src/components/vkpi/pages/DataQualityPage.tsx
frontend/src/services/vkpi/data-quality-api.ts
tests/test_vkpi_data_quality_domain.py
```

## Line Counts

| File | Lines |
|---|---:|
| `frontend/src/components/vkpi/pages/DataQualityPage.tsx` | 305 |
| `frontend/src/domains/data-quality/api.ts` | 24 |
| `frontend/src/domains/data-quality/hooks.ts` | 41 |
| `frontend/src/domains/data-quality/index.ts` | 4 |
| `frontend/src/domains/data-quality/types.ts` | 25 |
| `frontend/src/domains/data-quality/components/DataQualitySummaryCards.tsx` | 59 |
| `backend/app/domains/data_quality/__init__.py` | 5 |
| `backend/app/domains/data_quality/service.py` | 11 |

All PoC files are below the 800-line cap.

## Verification

| Check | Result |
|---|---|
| Frontend build | Passed |
| Backend compileall | Passed |
| Targeted domain tests | `2 passed` |
| Full pytest | `450 passed, 125 warnings, 5 subtests passed` |
| Line guard | Still 32 known legacy violations, no new violation from PoC |

## Progress Impact

| Metric | Before | After |
|---|---:|---:|
| Domain directory skeleton | Present | Present |
| Real Domain business migration | 0 | First slice landed |
| External intelligence data | 0 | 0 |
| Provider/LLM execution | 0 | 0 |

This changes the Domain migration status from pure skeleton to first real business slice. It does not claim broad Domain completion.

## Follow-Up

1. Use this slice as the checklist template for the next API/domain migration.
2. Keep write/remediation actions out of the Data Quality domain until the write boundary is explicitly designed.
3. Do not mix Market Signal, Repair Center, or Dashboard rewrites into this PoC bucket.
4. Next candidate slice: move a second `vkpi.ui-api.ts` read-only API surface into its owning domain.
