# V-KPI P5.65 Product Fit Monitor

## Scope

P5.65 adds a read-only monitor for the Product Fit foundation created in
P5.62-P5.64. It does not change Product Fit ranking, enqueue sync, or call any
provider.

The monitor surfaces:

- SKU alias coverage and products missing aliases.
- SKU spec fact coverage and products missing facts.
- Ambiguous alias norms that map to multiple SKUs.
- Low spec completeness samples.
- Product launch alias misses.
- One sampled KOL x SKU dry-run result.

Warnings do not automatically fail the report. Coverage and table availability
fail the report; ambiguity and low completeness are expected to remain visible
until the product catalog is cleaned.

## Operator Command

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_product_fit_monitor.py \
  --query viltrox \
  --json-out runtime/ops/p5-65-product-fit-monitor.json \
  --md-out runtime/ops/p5-65-product-fit-monitor.md
```

## Acceptance

- `provider_calls=false`, `llm_calls=false`, `write_db=false`,
  `sync_triggered=false`.
- Alias/spec coverage is at least 95 percent of `vkpi_products`.
- Missing joins, ambiguous aliases, low completeness, and launch misses are
  visible as samples or warnings.
- Sample KOL x SKU dry-run passes.
