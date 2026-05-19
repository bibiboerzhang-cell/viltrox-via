# V-KPI P8 Competitor Brain v0

## Scope

P8 v0 builds a deterministic competitor signal layer from data already inside V-KPI.

P8-1 is preview-only:

```text
read P6 content brain fields
read P2/P3 legacy risk and VOC staging signals
read existing market scan skeleton tables if populated
produce JSON + Markdown preview
do not write competitor tables
do not call providers
do not run crawlers
```

## Non-Goals

P8 v0 does not include:

```text
live competitor crawling
LLM summarization
price monitoring
ad library scraping
Socialinsider parity
frontend UI
automatic alerts
recommendation scoring changes
```

Those can be separate P8.x or P9/P10 packages after deterministic preview is useful.

## Current Data Sources

### P6 Content Brain

```text
vkpi_industry_posts.brand_mentions_json
vkpi_industry_posts.product_intents_json
vkpi_industry_posts.risk_flags_json
vkpi_industry_posts.content_tags_json
vkpi_industry_posts.platform
vkpi_industry_posts.post_url
vkpi_industry_posts.title / caption
vkpi_industry_posts.published_at
```

Current local snapshot:

```text
analyzed_posts=3
brands.godox=3
products.FL15Bi=2
products.FL15B=2
risks.pricing_sensitive=3
risks.competitor_focus=3
```

### Legacy Risk / VOC

```text
vkpi_legacy_risk_watchlist_staging rows=13
vkpi_legacy_voc_alerts_staging rows=37
```

These are staging sources. P8 preview may use them as signals, but must retain `source_sheet` and `source_row` in evidence.

### Market Scan Skeletons

Existing tables:

```text
vkpi_market_scan_runs       rows=0
vkpi_market_sources         existing table
vkpi_market_mentions        existing table
vkpi_competitor_products    rows=0
vkpi_competitor_content     rows=0
```

P8-1 should not write these tables. P8-3 can decide whether to use the existing skeleton or add a narrow competitor signal table.

## Entity Model

P8 preview treats competitor brain as signal aggregation, not canonical product matching.

Core objects:

```text
competitor_brand
product_hint
signal_type
source
evidence
severity
recency
```

Signal types:

```text
competitor_mention
competitor_focus
pricing_sensitive
voc_issue
risk_watch
product_comparison
launch_overlap
```

## Preview Output

P8-1 JSON shape:

```json
{
  "scenario": "p8_competitor_brain_preview",
  "provider_calls": false,
  "summary": {
    "competitor_brands": 1,
    "signals": 3,
    "risk_sources": 13,
    "voc_sources": 37
  },
  "competitors": [
    {
      "brand": "godox",
      "signal_count": 3,
      "risk_count": 3,
      "product_hints": ["FL15Bi", "FL15B"],
      "top_signal_types": {
        "competitor_focus": 3,
        "pricing_sensitive": 3
      },
      "evidence": [
        {
          "source_table": "vkpi_industry_posts",
          "source_id": 123,
          "source_url": "https://...",
          "signal_type": "competitor_focus",
          "detail": "brand_mentions_json contains competitor brand godox",
          "published_at": "2026-05-07T02:00:21Z"
        }
      ]
    }
  ]
}
```

Markdown output should show:

```text
Top competitor brands
Top product hints
Risk/VOC evidence
Source traceability
Current data limitations
```

## Scoring

P8-1 uses deterministic signal scoring only:

```text
competitor_focus        +30
product_comparison      +25
pricing_sensitive       +15
voc_issue               +15
risk_watch              +10
recent_90d              +5
```

No recommendation action should depend on this score until P8-3 stores and reviews the output.

## CLI

P8-1 should add:

```bash
python3 scripts/p8_competitor_brain.py \
  --limit 20 \
  --json-out /tmp/p8_competitor_brain.json \
  --md-out /tmp/p8_competitor_brain.md
```

Defaults:

```text
dry-run=true
provider_calls=false
write_db=false
```

Forbidden in P8-1:

```text
--commit
--provider
--crawl
--record-cost
```

## Acceptance Gates

P8-1 is accepted only if:

```text
1. python3 -m py_compile passes for service + CLI.
2. CLI returns JSON and Markdown preview.
3. provider_calls=false.
4. vkpi_ai_cost_ledger count remains unchanged.
5. Every signal has source_table and source_id or source_sheet + source_row.
6. Existing competitor tables remain row-count unchanged.
7. No frontend build is required.
```

## Package Plan

```text
P8-0  design this deterministic preview boundary
P8-1  service + CLI preview from P6/P2 sources
P8-2  optional schema decision: reuse market scan tables or add competitor signal table
P8-3  commit reviewed competitor signals
P8-4  read-only review API/UI
```

P8 stops after deterministic preview unless the output is useful enough to justify storage.
