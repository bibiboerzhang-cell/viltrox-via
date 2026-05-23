# V-KPI P5.67 Reddit Stability Strategy

## Scope

P5.67 clarifies Reddit as a bounded market-signal source. It does not crawl
Reddit, does not run Apify, and does not write market rows.

## Decision

Reddit support is allowed only as:

1. **OAuth/PRAW first** when `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and
   `praw` are available.
2. **Public JSON best-effort** for allowlisted subreddits only. This path is
   explicitly incomplete and can be blocked/rate-limited by Reddit.
3. **Apify fallback** only after budget approval.

V-KPI must not claim full Reddit coverage.

## Limits

- subreddit posts default: `25`, hard cap: `100`
- brand search default: `25`, hard cap: `50`
- post comments default: `100`, hard cap: `300`
- comment depth default: `3`, hard cap: `5`

## Watchlist First

Initial Reddit collection, when approved later, should be watchlist-only:

- photography
- videography
- cinematography
- SonyAlpha
- fujifilm
- nikon
- M43

## Storage

Raw source observations should write to:

- `vkpi_market_scan_runs`
- `vkpi_market_sources`
- `vkpi_market_mentions`

Reviewed signals may later write to:

- `vkpi_competitor_signals`

## Acceptance

Run:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_reddit_stability_strategy.py \
  --json-out runtime/ops/p5-67-reddit-stability-strategy.json \
  --md-out runtime/ops/p5-67-reddit-stability-strategy.md
```

Acceptance requires:

- Reddit crawler is registered.
- provider paths are classified.
- best-effort mode is explicit.
- no full Reddit promise exists.
- external calls, writes, provider calls, and sync remain blocked.
