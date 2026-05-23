# V-KPI P5.63 SKU Spec Readiness

## Scope

P5.63 creates a normalized spec facts layer from the existing official product
catalog. It does not crawl product pages, call providers, or change Product Fit
ranking.

The facts table is `vkpi_product_spec_facts`. It keeps raw official catalog
data in `vkpi_products.specs_json` intact and extracts fields needed by later
Product Fit logic:

- mount and normalized mount
- focal length min/max
- max aperture
- weight in grams
- filter size
- price
- product URL and source confidence
- completeness and missing-field status

## Operator Checks

Dry-run:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_sku_spec_readiness.py \
  --limit 500 \
  --json-out runtime/ops/p5-63-sku-spec-readiness.json \
  --md-out runtime/ops/p5-63-sku-spec-readiness.md
```

Local SQLite schema check:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_sku_spec_readiness.py --limit 500 --ensure-schema
```

Apply normalized facts after reviewing missing-field counts:

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_sku_spec_readiness.py --limit 500 --apply
```

## Acceptance

- `provider_calls=false`, `llm_calls=false`, `sync_triggered=false`.
- `product_count > 0` and `fact_count > 0`.
- `vkpi_product_spec_facts` exists after migration.
- Lens-like SKUs have at least one complete core spec fact.
- Missing fields are counted and sampled instead of silently treated as
  complete.
