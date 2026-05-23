# V-KPI P5.66 Market Signal Source Design

## Scope

P5.66 defines the market-signal source contract before any new crawling starts.
It is intentionally design-only and read-only.

No external collection is allowed in this step:

- no Reddit crawl
- no X crawl
- no RSS fetch
- no competitor site fetch
- no YouTube search
- no Apify, Gemini, or LLM call

## Source Gates

| Source | Path | Gate |
|---|---|---|
| Reddit community posts | OAuth or best-effort public JSON, Apify fallback only after approval | P5.67 |
| X public posts/comments | Apify or official API only after validation | P5.68 |
| RSS industry news | allowlisted feed fetcher | P5.69 |
| Competitor official sites | allowlisted page hash diff | P5.69 |
| YouTube review/search watch | YouTube API first, Apify quota fallback | market v0 after data trust |

## Canonical Contract

Every future market signal must carry:

- identity: `source_uid`, `source_type`, `platform`, `source_url`
- provenance: `provider`, `provider_run_id`, `captured_at`, `raw_payload_hash`, `terms_gate`
- content: `title`, `text`, `author_or_channel`, `published_at`, `language`
- metrics: `views`, `likes`, `comments_count`, `score`
- classification: `signal_type`, `brand`, `product_hint`, `sentiment`, `confidence`
- review: `review_status`, `reviewed_by`, `reviewed_at`, `decision_note`

## Storage Direction

Raw source records should flow through the existing market scan skeletons:

- `vkpi_market_scan_runs`
- `vkpi_market_sources`
- `vkpi_market_mentions`

Reviewed competitor observations should flow into:

- `vkpi_competitor_signal_runs`
- `vkpi_competitor_signals`

No source should write directly into recommendation ranking or Product Fit.

## Acceptance

Run:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_market_source_design.py \
  --json-out runtime/ops/p5-66-market-source-design.json \
  --md-out runtime/ops/p5-66-market-source-design.md
```

Acceptance requires:

- existing market scan and competitor signal tables are present
- every source has a required source contract
- all external calls are blocked
- all writes are blocked
- Reddit and X remain behind their separate gates
