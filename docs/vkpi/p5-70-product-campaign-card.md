# V-KPI P5.70 Product Campaign Card

P5.70 creates a read-only campaign planning card for one product SKU. It combines official product spec facts, aliases, local KOL records, and existing competitor signals.

## Scope

- Select a SKU from `vkpi_product_spec_facts`.
- Score KOL candidates from `vkpi_kol_pool` using local profile text, Viltrox fit score, audience scale, and SKU/alias/spec evidence.
- Surface market risk from `vkpi_competitor_signals`.
- Return campaign actions for human planning.

It does not create campaigns, projects, tasks, short links, provider calls, sync runs, or recommendations.

## API

```text
GET /api/admin/vkpi/industry-data/product-campaign-card?sku=AF-35/1.8-FE
```

## CLI

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_product_campaign_card.py \
  --json-out runtime/ops/p5-70-product-campaign-card.json \
  --md-out runtime/ops/p5-70-product-campaign-card.md \
  --json
```

## Acceptance

- `provider_calls=false`
- `llm_calls=false`
- `write_db=false`
- `sync_triggered=false`
- card includes product, KOL candidates, market risk, and human actions
