# V-KPI P6-5 Content Brain Review Surface

## Scope

P6-5 exposes content brain analysis results through read-only API and frontend review UI.

It reads:

```text
vkpi_industry_posts
vkpi_industry_accounts
vkpi_industry_post_media
vkpi_provider_budget_caps
```

It does not:

```text
run analysis
call LLM providers
write post/media analysis fields
write account snapshot rows
write AI cost ledger rows
create alerts
```

## API

```text
GET /api/admin/vkpi/industry-data/content-brain/status
GET /api/admin/vkpi/industry-data/content-brain/posts
```

Post list filters:

```text
status
platform
query
limit
```

## Frontend

The Data Analysis page now includes a `内容脑分析` panel with:

```text
coverage metrics
status/tag/risk/product distributions
status/platform/search filters
post-level analysis list
```

The panel is read-only. It does not expose commit, provider, or retry controls.

## Verified Result

Service smoke:

```text
schema_ready=True
post_count=10
analyzed_count=3
coverage_ratio=0.3
posts_returned=3
```

Frontend:

```text
npm run build passed
```

## Acceptance Gates

```text
1. Backend py_compile passes.
2. Frontend build passes.
3. Status endpoint returns coverage and distributions.
4. Post endpoint returns parsed tags/products/risks/brands.
5. UI uses read-only GET APIs only.
6. No provider call or AI cost ledger write occurs.
```
