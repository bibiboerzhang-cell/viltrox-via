# V-KPI P9-2 Natural Search API

## Scope

P9-2 exposes deterministic natural search through a read-only admin API.

## API

```text
GET /api/admin/vkpi/search?q=<query>&limit=<n>
```

Permissions:

```text
require_tab("vkpi", "read")
```

The route reuses:

```text
backend/app/services/vkpi/natural_search.py
```

It does not:

```text
call providers
write search logs
create frontend UI
create embeddings
```

## Files

```text
backend/app/api/routers/vkpi_search.py
backend/app/main.py
docs/vkpi/p9-2-natural-search-api.md
```

## Verified Result

Service smoke for the API payload:

```text
query=godox pricing
total=3
provider_calls=False
write_db=False
top_source=vkpi_competitor_signals
ai_cost_before=0
ai_cost_after=0
```

## Acceptance

```text
python3 -m py_compile backend/app/services/vkpi/natural_search.py passed
python3 -m py_compile backend/app/api/routers/vkpi_search.py passed
python3 -m py_compile backend/app/main.py passed
git diff --check passed
```

## Next

P9-3 can add a compact frontend search panel.

Keep it read-only and show `source_table:source_id` for every result.
