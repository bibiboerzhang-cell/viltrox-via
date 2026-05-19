# V-KPI P9-3 Natural Search Frontend

## Scope

P9-3 adds a compact read-only natural search panel to Data Analysis.

It uses:

```text
GET /api/admin/vkpi/search
```

It does not:

```text
write search logs
call providers
create saved searches
modify result records
```

## Files

```text
frontend/src/services/vkpi.ui-api.ts
frontend/src/components/vkpi/pages/data-analysis/NaturalSearchPanel.tsx
frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx
frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css
docs/vkpi/p9-3-natural-search-frontend.md
```

## UI

The panel shows:

```text
query input
total
provider_calls
write_db
tokens
result_type
score
source_table:source_id
title
evidence snippet
```

Default query:

```text
godox pricing
```

## Acceptance

```text
python3 -m py_compile backend/app/services/vkpi/natural_search.py passed
python3 -m py_compile backend/app/api/routers/vkpi_search.py passed
python3 -m py_compile backend/app/main.py passed
npm run build passed
git diff --check passed
```

## Next

P9 completion can be documented, then P10 Learning Loop can start from existing feedback and pending review surfaces.
