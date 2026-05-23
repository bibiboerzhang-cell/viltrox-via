# V-KPI P5.64 KOL x SKU Fit Dry Run

## Scope

P5.64 introduces a read-only rules report for `KOL x SKU` Product Fit. It uses
the SKU alias table from P5.62 and normalized spec facts from P5.63.

This step does not write `viltrox_fit_score`, change recommendation ranking, or
call Apify, Gemini, or LLM providers.

## Inputs

- `vkpi_kol_pool`: existing KOL profile, bio, raw platform payload, and product
  hints.
- `vkpi_product_aliases`: canonical SKU aliases.
- `vkpi_product_spec_facts`: normalized SKU specs.
- `vkpi_kol_profile_deep.dimensions_11_json`: optional existing 11D product
  fit evidence when present.

## Acceptance

- `provider_calls=false`, `llm_calls=false`, `write_db=false`,
  `sync_triggered=false`.
- One KOL is selected.
- SKU facts and aliases are available.
- Top SKU candidates are generated.
- Every top candidate has rule evidence.

## Operator Command

```bash
PYTHONPATH=backend .venv/bin/python scripts/vkpi_kol_sku_fit_dry_run.py \
  --query viltrox \
  --top-n 12 \
  --json-out runtime/ops/p5-64-kol-sku-fit-dry-run.json \
  --md-out runtime/ops/p5-64-kol-sku-fit-dry-run.md
```
