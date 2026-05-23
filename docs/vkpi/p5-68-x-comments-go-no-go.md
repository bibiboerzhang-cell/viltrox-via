# V-KPI P5.68 X Comments Go/No-Go

P5.68 is a gate, not a crawler launch. It defines how to validate X comments with exactly 14 selected targets before any ongoing collection, daily timer, or recommendation dependency is allowed.

## Decision

- Default state: hold.
- Allowed validation: one manually approved 14-target run.
- Provider path: X official API replies when `X_BEARER_TOKEN` is approved, otherwise Apify only when both `APIFY_TOKEN` and `APIFY_X_COMMENTS_ACTOR_ID` are configured.
- Storage path: write validation artifacts first. Only reviewed signals can later move into `vkpi_market_sources`, `vkpi_market_mentions`, or `vkpi_competitor_signals`.
- No daily X collection, no broad X search, no full-X claim.

## Validation Input

The target CSV must contain exactly 14 rows and these columns:

- `target_id`
- `source_url`
- `brand_context`
- `expected_signal`

Each `source_url` must be an X/Twitter status URL or tweet id. The report validates format only; it does not fetch.

## Stop Rules

- Stop immediately when provider errors reach 3.
- Stop immediately if cost or rate limit exceeds the approved validation budget.
- Do not retry failed targets more than once during validation.
- Do not promote to daily collection from validation results alone.

## Go Criteria

- 14 selected X post URLs or tweet ids are provided before execution.
- Provider path is explicit: X official API replies or Apify comments actor.
- Errors stay below 3 and provider run artifacts are saved.
- At least 8 of 14 targets return usable comments or explicit no-comments status.
- At least 5 targets contain product, brand, competitor, complaint, launch, or creator conversation signal.
- Estimated cost and runtime are recorded before any continuation.

## CLI

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_x_comments_go_no_go.py \
  --json-out runtime/ops/p5-68-x-comments-go-no-go.json \
  --md-out runtime/ops/p5-68-x-comments-go-no-go.md \
  --json
```

With target validation:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_x_comments_go_no_go.py \
  --targets-file runtime/ops/x-comments-validation-targets.csv \
  --json-out runtime/ops/p5-68-x-comments-go-no-go.json \
  --md-out runtime/ops/p5-68-x-comments-go-no-go.md
```

## Acceptance

- `provider_calls=false`
- `external_http_calls=false`
- `write_db=false`
- `sync_triggered=false`
- `passed=true`
- `decision` explains why the provider run is held or ready for explicit approval
