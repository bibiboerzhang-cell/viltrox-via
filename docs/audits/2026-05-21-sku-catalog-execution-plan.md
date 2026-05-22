# SKU Catalog Execution Plan

Date: 2026-05-21

## Decision

V-KPI needs a product catalog that is separate from the product cost catalog.

- `vkpi_product_cost_catalog` remains the management-only internal cost table.
- `vkpi_products` is the official product/SKU catalog for sales price, mount, specs, source URL, and Product Fit tags.
- Product data must be source-backed. Do not fill fake specs, fake prices, or fake mount variants.

## Started In This Package

Added the first verified official SKU seed:

- SKU: `AF-35MM-F18-EVO-FE`
- Name: `AF 35mm F1.8 EVO FE`
- Series: `EVO`
- Mount: `E-mount`
- Sales price: `$395.00`
- Source: `https://viltrox.com/products/af-35mm-f1-8-fe`
- Specs: lens mount, lens elements, focal length, viewing angle, aperture range, blades, shooting distance, focus mechanism, motor, mode, magnification, size, weight, filter size

## Execution Order

1. SKU-0 foundation
   - Extend `vkpi_products` with `series`, `mount`, `product_url`, `specs_json`, `fit_tags_json`, `source_url`, `source_checked_at`, and `source_confidence`.
   - Add import script for source-backed seeds.
   - Acceptance: local import can upsert verified SKU and API returns specs/mount/price.

2. SKU-1 official product inventory
   - Crawl or manually seed official Viltrox product URLs in small batches.
   - Separate product variants by mount, not only by lens name.
   - Acceptance: each row has `source_url`; partial rows are marked lower confidence instead of pretending complete.

3. SKU-2 Settings SKU module
   - Show SKU, product name, category, series, mount, sales price, and source confidence.
   - Add a specs drawer or expandable row.
   - Acceptance: staff can distinguish FE/Z/X/N variants without opening Viltrox.com.

4. SKU-3 Product Fit integration
   - Replace hardcoded Product Fit keywords with catalog-derived `fit_tags_json` plus verified spec fields.
   - Acceptance: Product Fit can explain why a KOL maps to a specific SKU or mount variant.

5. SKU-4 online rollout
   - Deploy migration.
   - Import seed rows online.
   - Verify `/api/marketing/product-catalog?query=AF-35MM-F18-EVO-FE` returns source-backed specs.

## Guardrails

- Do not write sales price into `vkpi_product_cost_catalog`.
- Do not mark a SKU `verified` unless price/specs came from a source URL.
- If a product page has multiple mount variants, create separate SKU rows.
- If a page only gives marketing copy but no specs, import as `status='needs_specs'` or skip.
- Product Fit may use low-confidence rows for discovery, but UI must show confidence.
