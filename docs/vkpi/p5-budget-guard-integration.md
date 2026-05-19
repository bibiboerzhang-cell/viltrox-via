# V-KPI P5 Budget Guard Integration

## Scope

P5 first hardens the LLM provider path before P4.2 can generate natural-language recommendation reasons.

This package covers:

```text
llm_gateway.invoke / chat
llm_gateway.record_call
vkpi_ai_cost_ledger
vkpi_provider_budget_caps
```

This package does not cover frontend budget pages or non-LLM crawler providers.

## Gateway Rules

Every real LLM provider attempt must pass all configured budget scopes:

```text
monthly_total
provider:<provider>
cron:<purpose>
```

Provider scope mapping:

```text
openai    -> provider:openai
google    -> provider:gemini
anthropic -> provider:claude
```

If a required scope is missing or hard-stopped, the gateway skips the provider and falls back to `rule_v0`.

## Cost Ledger Rules

Only real provider success writes `vkpi_ai_cost_ledger`.

Fallbacks do not write cost ledger rows:

```text
empty_prompt
budget_disabled
ai_budget_hard_stop
all_providers_failed
rule_v0 fallback
```

When a real provider succeeds, one ledger row is inserted and budget spend is applied to:

```text
cron:<purpose>
provider:<provider>
monthly_total
```

## Seeded Scopes

P5 adds default caps for LLM purposes:

```text
cron:vkpi_weekly_report
cron:vkpi_pillar
cron:p4_recommendation_reasons
```

Existing P3.5 scopes remain:

```text
monthly_total
provider:openai
provider:claude
provider:gemini
provider:apify
cron:p4_recommendations_daily
cron:p4_market_signals_refresh
```

## P4.2 Gate

P4.2 can only call LLM reason generation through `llm_gateway.invoke` with:

```text
purpose="p4_recommendation_reasons"
```

The gateway derives:

```text
cost_scope="cron:p4_recommendation_reasons"
```

Ranking remains deterministic from P4.1. P4.2 may write wording only.

## Acceptance

```text
1. All P5 budget scopes exist in vkpi_provider_budget_caps.
2. llm_gateway blocks missing or hard-stopped required budget scopes.
3. llm_gateway checks monthly_total, provider scope, and cron scope before provider calls.
4. Real provider success records one vkpi_ai_cost_ledger row.
5. Cost is applied to monthly_total, provider scope, and cron scope.
6. Fallback/rule_v0 paths do not create cost ledger rows.
7. P4.1 dry-run still leaves vkpi_ai_cost_ledger unchanged.
```
