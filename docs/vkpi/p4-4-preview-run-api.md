# V-KPI P4-4 Preview Run API

## Scope

P4-4 exposes persisted P4 preview runs for review. It does not add frontend layout work or create new recommendation tables.

## API

```text
GET /api/admin/vkpi/product-analysis/recommendation-runs
```

Query parameters:

```text
strategy_version   optional, e.g. new_launch_match_v1
status             optional, e.g. previewed
limit              default 100, max 300
```

Response:

```json
{
  "runs": [
    {
      "id": 518,
      "run_uid": "p4nlm-...",
      "strategy_version": "new_launch_match_v1",
      "status": "previewed",
      "candidate_count": 1012,
      "recommendation_count": 3,
      "filters": {
        "scenario": "new_launch_match",
        "product_query": "AF 35mm F1.2 LAB FE"
      },
      "recommendation_status_counts": {
        "previewed": 3
      }
    }
  ]
}
```

Existing item and evidence endpoints remain unchanged:

```text
GET /api/admin/vkpi/product-analysis/recommendations?run_id=<id>
GET /api/admin/vkpi/product-analysis/recommendations/<recommendation_id>/evidence
```

## Frontend Service

`frontend/src/services/vkpi.ui-api.ts` now exposes:

```text
listProductRecommendationRuns(token, { strategyVersion, status, limit })
```

No page-level UI is changed in P4-4.

## Acceptance Gates

```text
1. Run list filters by strategy_version.
2. Run list filters by status.
3. Each run includes parsed filters.
4. Each run includes recommendation_status_counts.
5. Existing recommendation list and evidence endpoints remain unchanged.
6. No frontend route or layout changes are included.
```
