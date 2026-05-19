# V-KPI P6 Content Brain v0

## Scope

P6 starts with schema only.

This package adds content-analysis fields to:

```text
vkpi_industry_posts
vkpi_industry_post_media
```

It does not:

```text
write analyzer code
call LLM providers
write account snapshot labels
overwrite raw platform facts
change recommendation scoring
```

## Post-Level Fields

`vkpi_industry_posts` receives:

```text
content_tags_json
product_intents_json
risk_flags_json
brand_mentions_json
ai_summary
analyzed_at
analysis_version
analysis_status
analysis_error
```

JSON-shaped data remains `TEXT`, matching the current cross-runtime convention.

## Media-Level Table

`vkpi_industry_post_media` stores one or more media assets per post:

```text
media_uid
post_id
media_url
thumbnail_url
media_type
duration_seconds
source_platform
source_json
content_tags_json
product_intents_json
risk_flags_json
brand_mentions_json
ai_summary
analyzed_at
analysis_version
analysis_status
analysis_error
metadata_json
```

The table uses `post_id` to keep every media analysis auditable back to the source post.

## Rules

```text
1. Account snapshot rows remain raw account metrics only.
2. Post analysis can be retried by setting analysis_status back to pending.
3. Failed analysis stores analysis_error without clearing raw post/media facts.
4. Re-analysis updates only analysis fields, not original crawl fields.
5. P6 analyzer must use Budget Guard before any real provider call.
```

## Acceptance Gates

```text
1. 062 migration and down migration both exist.
2. 062 is registered in _POSTGRES_MIGRATION_SEQUENCE.
3. Post-level fields are on vkpi_industry_posts.
4. Media-level fields are on vkpi_industry_post_media.
5. No fields are added to vkpi_industry_account_snapshots.
6. No analyzer/API/frontend code is included in this schema package.
```
