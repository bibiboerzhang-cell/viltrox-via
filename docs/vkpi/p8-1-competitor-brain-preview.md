# V-KPI P8-1 Competitor Brain Preview

## Scope

P8-1 implements deterministic competitor brain preview.

It reads:

```text
vkpi_industry_posts
vkpi_legacy_voc_alerts_staging
vkpi_legacy_risk_watchlist_staging
```

It does not:

```text
write vkpi_competitor_products
write vkpi_competitor_content
write vkpi_market_scan_runs
call LLM providers
run crawlers
write vkpi_ai_cost_ledger
create frontend UI
```

## Files

```text
backend/app/services/vkpi/competitor_brain.py
scripts/p8_competitor_brain.py
docs/vkpi/p8-1-competitor-brain-preview.md
```

## CLI

```bash
python3 scripts/p8_competitor_brain.py --limit 10
```

Output files:

```bash
python3 scripts/p8_competitor_brain.py \
  --limit 5 \
  --json-out /tmp/p8_competitor_brain.json \
  --md-out /tmp/p8_competitor_brain.md
```

Forbidden flags:

```text
--commit
--write-db
--provider
--crawl
--record-cost
```

## Current Result

```text
scenario=p8_competitor_brain_preview
provider_calls=false
write_db=false
competitor_brands=7
signals=25
content_signals=9
voc_signals=16
risk_watch_signals=0
```

Top competitors:

```text
1. godox     score=240 signals=9 risk_count=6
2. fujifilm  score=90  signals=6 risk_count=6
3. canon     score=45  signals=3 risk_count=3
4. nikon     score=45  signals=3 risk_count=3
5. sony      score=30  signals=2 risk_count=2
6. leica     score=15  signals=1 risk_count=1
7. sigma     score=15  signals=1 risk_count=1
```

Data quality fixes included in P8-1:

```text
fuji -> fujifilm brand alias normalization
generic product hints such as All/F1/FE filtered out
risk_count includes competitor_focus, pricing_sensitive, voc_issue, and risk_watch
```

## Traceability

Every evidence item includes at least one of:

```text
source_table + source_id
source_sheet + source_row
source_url
```

Examples:

```text
godox:
  source=vkpi_industry_posts:580
  signal_type=competitor_mention
  detail=brand_mentions_json contains competitor brand godox

fujifilm:
  source=vkpi_legacy_voc_alerts_staging:81
  source_sheet=海外舆情监控表
  source_row=8
  signal_type=voc_issue
```

## Acceptance

```text
python3 -m py_compile backend/app/services/vkpi/competitor_brain.py passed
python3 -m py_compile scripts/p8_competitor_brain.py passed
python3 scripts/p8_competitor_brain.py --limit 10 passed
python3 scripts/p8_competitor_brain.py --json passed
json_out and md_out files were generated
vkpi_competitor_products rows remain 0
vkpi_competitor_content rows remain 0
vkpi_market_scan_runs rows remain 0
vkpi_ai_cost_ledger rows remain 0
```

## Next

P8-2 should decide storage shape.

Recommendation: add a narrow reviewed signal table instead of writing directly into `vkpi_competitor_products`, because P8-1 signals are source observations, not canonical competitor product definitions.
