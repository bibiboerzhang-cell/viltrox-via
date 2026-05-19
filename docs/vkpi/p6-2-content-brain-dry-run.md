# V-KPI P6-2 Content Brain Dry-Run

## Scope

P6-2 adds a deterministic content-analysis preview for industry posts.

It reads:

```text
vkpi_industry_posts
vkpi_industry_accounts
vkpi_industry_post_media (optional, only when table exists)
vkpi_provider_budget_caps
```

It does not:

```text
write vkpi_industry_posts analysis fields
write vkpi_industry_post_media analysis fields
call LLM providers
record AI cost ledger rows
change recommendation scoring
create alerts
```

## CLI

```bash
python3 scripts/p6_content_brain.py \
  --limit 50 \
  --json-out /tmp/p6_content_brain_preview.json \
  --md-out /tmp/p6_content_brain_preview.md
```

Filters:

```text
--platform <platform>
--account-id <id>
--post-id <id>
--query <text>
--include-media
```

Forbidden flags:

```text
--commit
--write
--persist
--save
--apply
--with-llm
--provider
--record-cost
```

## Deterministic Output

Each preview item includes:

```text
content_tags_json
product_intents_json
risk_flags_json
brand_mentions_json
ai_summary
evidence
media_preview
```

The analysis version is:

```text
p6_content_brain_rule_v0
```

## Budget Guard

P6-2 calls Budget Guard with:

```text
scope=cron:p6_content_brain_analysis
estimated_cost_usd=0.0
```

No provider is called and no cost ledger row is recorded.

## Verified Command

```bash
python3 scripts/p6_content_brain.py \
  --limit 5 \
  --json-out /tmp/p6_content_brain_preview.json \
  --md-out /tmp/p6_content_brain_preview.md
```

Observed summary:

```text
scenario=p6_content_brain_dry_run
analysis_version=p6_content_brain_rule_v0
posts_evaluated=5
returned=5
budget_scope=cron:p6_content_brain_analysis
budget_allowed=true
provider_calls=false
writes_enabled=false
tag_types=7
risk_types=2
```

AI cost ledger stayed at:

```text
total_calls=0
total_spend=0
```

## Acceptance Gates

```text
1. py_compile passes for content_brain service and CLI.
2. CLI returns preview rows from real vkpi_industry_posts data.
3. JSON and Markdown outputs are generated when requested.
4. provider_calls=false.
5. writes_enabled=false.
6. vkpi_ai_cost_ledger remains unchanged.
7. Forbidden write/provider flags are rejected before analysis.
8. Missing vkpi_industry_post_media table does not break post-level dry-run.
```
