# V-KPI P6-4 Content Brain Commit Analysis

## Scope

P6-4 allows deterministic content brain preview output to be committed into the post/media analysis fields added by P6-1.

It writes only:

```text
vkpi_industry_posts.content_tags_json
vkpi_industry_posts.product_intents_json
vkpi_industry_posts.risk_flags_json
vkpi_industry_posts.brand_mentions_json
vkpi_industry_posts.ai_summary
vkpi_industry_posts.analyzed_at
vkpi_industry_posts.analysis_version
vkpi_industry_posts.analysis_status
vkpi_industry_posts.analysis_error

vkpi_industry_post_media.* analysis fields when --include-media is used and rows exist
```

It does not:

```text
change raw crawl fields
change account snapshot rows
call LLM providers
record AI cost ledger rows
create alerts
change recommendation rows
```

## CLI

Dry-run remains the default:

```bash
python3 scripts/p6_content_brain.py --limit 5
```

Commit requires explicit confirmation:

```bash
python3 scripts/p6_content_brain.py \
  --limit 3 \
  --commit-analysis \
  --confirm
```

Re-analysis of rows already marked `done` requires:

```text
--force
```

## Retry Rules

```text
analysis_status=pending    can be committed
analysis_status=failed     can be committed
analysis_status=done       skipped unless --force
```

Raw platform facts remain unchanged.

## Acceptance Gates

```text
1. --commit-analysis without --confirm is rejected.
2. Dry-run still reports writes_enabled=false.
3. Commit mode reports writes_enabled=true.
4. Commit mode updates only analysis columns.
5. vkpi_ai_cost_ledger remains unchanged.
6. Re-running commit without --force skips rows already marked done.
```

## Verified Result

```bash
python3 scripts/p6_content_brain.py \
  --limit 3 \
  --commit-analysis \
  --confirm \
  --json-out /tmp/p6_content_brain_commit.json \
  --md-out /tmp/p6_content_brain_commit.md
```

Observed summary:

```text
scenario=p6_content_brain_commit_analysis
posts_evaluated=3
returned=3
provider_calls=false
writes_enabled=true
write_mode=commit_analysis
posts_updated=3
media_updated=0
skipped_done=0
```

Re-run without `--force`:

```text
posts_updated=0
skipped_done=3
```

AI cost ledger stayed:

```text
calls=0
spend=0
```
