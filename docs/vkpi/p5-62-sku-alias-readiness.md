# V-KPI P5.62 SKU Alias Readiness

## Scope

P5.62 adds the official SKU alias foundation for Product Fit. It creates the
`vkpi_product_aliases` table and a read-only readiness report that generates
aliases from existing `vkpi_products` rows.

This step does not change recommendation ranking, does not trigger sync, and
does not call Apify, Gemini, or any LLM provider.

## Data Contract

`vkpi_product_aliases` stores normalized aliases by canonical `sku`:

- `sku`: canonical product key from `vkpi_products`.
- `alias`: human-readable alias, such as SKU, model name, official handle, or
  focal/aperture combination.
- `alias_norm`: normalized comparison key.
- `alias_type`: source class, such as `sku`, `model`, `marketing`,
  `official_handle`, `spec_combo`, or `fit_tag`.
- `confidence`: alias confidence from `0.00` to `1.00`.

The table is intentionally separate from Product Fit scoring. Later P5 work can
join through this table without changing the official catalog itself.

## Operator Checks

Run a dry-run report:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_sku_alias_readiness.py \
  --limit 500 \
  --json-out runtime/ops/p5-62-sku-alias-readiness.json \
  --md-out runtime/ops/p5-62-sku-alias-readiness.md
```

For local SQLite verification only, create the schema explicitly:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_sku_alias_readiness.py --limit 500 --ensure-schema
```

Apply generated aliases only after reviewing ambiguity:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_sku_alias_readiness.py --limit 500 --apply
```

## Acceptance

- `provider_calls=false`, `llm_calls=false`, `sync_triggered=false`.
- `product_count > 0`.
- `generated_alias_count > 0`.
- `vkpi_product_aliases` exists after migration.
- Ambiguous aliases are reported instead of silently joined.
- Launch probe reports exact alias hits and miss samples when
  `vkpi_product_launches` exists.
