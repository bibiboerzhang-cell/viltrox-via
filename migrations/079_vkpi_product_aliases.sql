-- P5.62 SKU alias readiness.
--
-- This table normalizes official SKU/model/marketing/spec aliases so Product
-- Fit can later join launches, evidence, and product catalog rows by the same
-- product identity instead of ad hoc string matching.

CREATE TABLE IF NOT EXISTS vkpi_product_aliases (
    id BIGSERIAL PRIMARY KEY,
    sku TEXT NOT NULL REFERENCES vkpi_products(sku) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'generated',
    source_table TEXT NOT NULL DEFAULT 'vkpi_products',
    source_id TEXT NOT NULL DEFAULT '',
    confidence NUMERIC(4,2) NOT NULL DEFAULT 0.50,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (sku, alias_norm)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_product_aliases_alias_norm
    ON vkpi_product_aliases(alias_norm);

CREATE INDEX IF NOT EXISTS idx_vkpi_product_aliases_sku
    ON vkpi_product_aliases(sku);

CREATE INDEX IF NOT EXISTS idx_vkpi_product_aliases_alias_type
    ON vkpi_product_aliases(alias_type);

CREATE INDEX IF NOT EXISTS idx_vkpi_product_aliases_confidence
    ON vkpi_product_aliases(confidence DESC);
