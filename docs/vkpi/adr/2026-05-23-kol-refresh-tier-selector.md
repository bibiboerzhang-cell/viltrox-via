# ADR: Qualified KOL Refresh Tier Selector

Date: 2026-05-23

## Context

V-KPI stopped the legacy daily full KOL pool refresh after the 466 retry closure. The 1021/1023 legacy KOL records should remain searchable historical records, not daily refresh targets.

KOL refresh can resume only after a selector limits scope to creators with current business relevance:

- recent Viltrox or SKU signal
- recent collaboration/project evidence
- active campaign
- recent sample/shipping evidence
- manual hot/watchlist flag
- future search-triggered stale-while-revalidate

This selector must come before Apify batch concurrency. Batch optimization must not be used to accelerate the old full-pool path.

## Decision

Add `vkpi_kol_refresh_tier` as the gate for KOL refresh:

- `hot`: eligible for future daily qualified refresh.
- `warm`: eligible for weekly or on-demand review, not daily by default.
- `cold`: searchable record only.

Initial daily selector consumes only `hot` rows unless an operator explicitly provides another tier list.

No tier table means no qualified KOL refresh. The code must return an empty selected set rather than falling back to `vkpi_kol_pool`.

## Tier Rules

`hot` if any condition is true:

- `manual_hot_flag`
- active project/campaign linked to the KOL
- sample/shipping evidence in the last 90 days
- project/collaboration evidence in the last 180 days
- Viltrox mention in the last 60 days
- SKU/product mention in the last 60 days

`warm` if:

- the KOL has a recent search marker in `vkpi_kol_refresh_tier`

Otherwise:

- `cold`

High score alone is not a daily-refresh reason in this version. This keeps legacy imported rows from becoming daily provider targets just because they look promising.

## Operational Boundary

The production timer remains official-only:

```bash
scripts/cron_daily_sync.py --official-max-posts 50 --skip-kol
```

Qualified KOL refresh requires an explicit operator flag:

```bash
scripts/cron_daily_sync.py --include-qualified-kol --kol-refresh-selector qualified --kol-tiers hot
```

Without `--kol-stale-before`, the qualified selector is catch-up mode and only
selects `hot` rows that have never been refreshed through
`vkpi_kol_refresh_tier.last_refresh_at`. Periodic refresh jobs must pass an
explicit cutoff, for example yesterday's UTC timestamp, so the job does not
reselect rows refreshed earlier in the same catch-up session.

Legacy full refresh remains separately guarded and still requires `--include-legacy-kol`.

## Acceptance

- Migration `076_vkpi_kol_refresh_tier.sql` creates the selector table.
- `scripts/vkpi_refresh_tier.py` can dry-run tier distribution without provider calls.
- `daily_sync` can plan/use `kol_refresh_selector=qualified`.
- Qualified catch-up does not refill the requested limit with already refreshed rows.
- If the tier table is absent, qualified selector returns zero rows and does not fall back to legacy full refresh.
- Apify batch implementation remains blocked until this selector has been initialized and reviewed.
