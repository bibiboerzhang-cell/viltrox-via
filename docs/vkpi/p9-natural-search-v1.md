# V-KPI P9 Natural Search v1

## Scope

P9 v1 starts with deterministic SQL search over existing V-KPI facts.

P9-1 does:

```text
parse a plain text query into lowercase tokens
search KOL pool
search Memory entities and facts
search recommendation previews
search competitor signals
search alerts
return evidence-linked results
```

P9-1 does not:

```text
call LLM providers
create embeddings
create a vector index
write a search index table
trigger crawlers
modify searched records
```

## Current Search Corpus

```text
vkpi_kol_pool                 1023 rows
vkpi_memory_entities          2966 rows
vkpi_memory_facts            15660 rows
vkpi_kol_recommendations        84 rows
vkpi_competitor_signals         25 rows
vkpi_alerts                     18 rows
```

## Result Schema

```json
{
  "query": "godox pricing",
  "provider_calls": false,
  "write_db": false,
  "total": 10,
  "items": [
    {
      "result_type": "competitor_signal",
      "title": "godox pricing_sensitive",
      "score": 42,
      "source_table": "vkpi_competitor_signals",
      "source_id": 7,
      "evidence": {
        "brand": "godox",
        "signal_type": "pricing_sensitive",
        "detail": "risk_flags_json contains pricing_sensitive"
      }
    }
  ]
}
```

## Ranking

Deterministic score:

```text
exact title/handle match       +30
token match in primary fields  +10 each
token match in evidence text    +4 each
source weight:
  kol_pool             +8
  memory_entity        +7
  memory_fact          +5
  recommendation       +6
  competitor_signal    +8
  alert                +6
```

P9-1 should favor traceability over clever interpretation.

## CLI

```bash
python3 scripts/p9_natural_search.py "godox pricing" --limit 10
python3 scripts/p9_natural_search.py "AF 35mm Germany" --json
```

## API

```text
GET /api/admin/vkpi/search?q=<query>&limit=20
```

## Acceptance Gates

```text
1. py_compile passes for service, router, and CLI.
2. CLI returns results for a competitor query.
3. CLI returns results for a KOL/product query.
4. provider_calls=false.
5. write_db=false.
6. vkpi_ai_cost_ledger remains unchanged.
7. Every result has source_table and source_id.
8. No frontend UI in P9-1.
```

## Package Plan

```text
P9-0  design deterministic search boundary
P9-1  service + CLI
P9-2  read-only API
P9-3  compact frontend search surface
P9-4  feedback hooks for poor results
```
