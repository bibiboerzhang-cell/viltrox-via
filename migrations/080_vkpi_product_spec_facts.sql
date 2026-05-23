-- P5.63 normalized official SKU specification facts.
--
-- Extracts lens/Product Fit relevant fields from vkpi_products without
-- overwriting the raw official specs_json payload.

CREATE TABLE IF NOT EXISTS vkpi_product_spec_facts (
    sku TEXT PRIMARY KEY REFERENCES vkpi_products(sku) ON DELETE CASCADE,
    category_main TEXT NOT NULL DEFAULT '',
    category_detail TEXT NOT NULL DEFAULT '',
    series TEXT NOT NULL DEFAULT '',
    mount TEXT NOT NULL DEFAULT '',
    mount_norm TEXT NOT NULL DEFAULT '',
    lens_mount TEXT NOT NULL DEFAULT '',
    lens_mount_norm TEXT NOT NULL DEFAULT '',
    focal_length_label TEXT NOT NULL DEFAULT '',
    focal_length_min_mm NUMERIC(8,2),
    focal_length_max_mm NUMERIC(8,2),
    max_aperture_label TEXT NOT NULL DEFAULT '',
    max_aperture_f NUMERIC(5,2),
    min_aperture_label TEXT NOT NULL DEFAULT '',
    weight_grams INTEGER,
    filter_size_mm INTEGER,
    price_usd NUMERIC(10,2),
    product_url TEXT NOT NULL DEFAULT '',
    fit_tags_json TEXT NOT NULL DEFAULT '[]',
    source_confidence NUMERIC(4,2) NOT NULL DEFAULT 0,
    completeness_score NUMERIC(5,2) NOT NULL DEFAULT 0,
    missing_fields_json TEXT NOT NULL DEFAULT '[]',
    raw_spec_fields_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_product_spec_facts_mount
    ON vkpi_product_spec_facts(mount_norm);

CREATE INDEX IF NOT EXISTS idx_vkpi_product_spec_facts_focal
    ON vkpi_product_spec_facts(focal_length_min_mm, focal_length_max_mm);

CREATE INDEX IF NOT EXISTS idx_vkpi_product_spec_facts_aperture
    ON vkpi_product_spec_facts(max_aperture_f);

CREATE INDEX IF NOT EXISTS idx_vkpi_product_spec_facts_completeness
    ON vkpi_product_spec_facts(completeness_score DESC);
