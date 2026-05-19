# V-KPI P9-1 Natural Search CLI

## Scope

P9-1 implements deterministic natural search service and CLI.

It searches:

```text
vkpi_kol_pool
vkpi_memory_entities
vkpi_memory_facts
vkpi_kol_recommendations
vkpi_competitor_signals
vkpi_alerts
```

It does not:

```text
call providers
create embeddings
write search index rows
modify source data
create frontend UI
```

## Files

```text
backend/app/services/vkpi/natural_search.py
scripts/p9_natural_search.py
docs/vkpi/p9-1-natural-search-cli.md
```

## CLI

```bash
python3 scripts/p9_natural_search.py "godox pricing" --limit 8
python3 scripts/p9_natural_search.py "AF 35mm Germany" --limit 8
python3 scripts/p9_natural_search.py "godox pricing" \
  --json-out /tmp/p9_godox.json \
  --md-out /tmp/p9_godox.md
```

## Query Behavior

P9-1 uses:

```text
token OR recall in SQL
Python-side deterministic scoring
source weights by table
small country alias expansion for English -> Chinese historical data
```

Example alias:

```text
Germany -> germany, 德国
```

## Verified Results

Competitor query:

```text
query=godox pricing
tokens=godox,pricing
provider_calls=false
write_db=false
total=8

top result:
  result_type=competitor_signal
  source=vkpi_competitor_signals:8
  title=godox pricing_sensitive
```

KOL country query:

```text
query=Germany
tokens=germany,德国
provider_calls=false
write_db=false
total=5

top result:
  result_type=kol_pool
  source=vkpi_kol_pool:4061
  title=Lukas Benjamin
```

Product query:

```text
query=AF 35mm Germany
tokens=af,35mm,germany,德国
provider_calls=false
write_db=false
total=8

top results:
  recommendation: AF 35mm F1.2 LAB
  recommendation: AF 35mm F1.8 EVO
  memory_entity: official_content AF 35mm
```

## Acceptance

```text
python3 -m py_compile backend/app/services/vkpi/natural_search.py passed
python3 -m py_compile scripts/p9_natural_search.py passed
competitor query returned source-linked results
product query returned source-linked results
country alias query returned KOL results
json_out and md_out were generated
provider_calls=false
write_db=false
```

## Next

P9-2 should expose:

```text
GET /api/admin/vkpi/search?q=<query>&limit=<n>
```

No frontend UI until the API returns stable result shapes.
