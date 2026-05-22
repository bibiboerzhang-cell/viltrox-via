-- Extend the V-KPI product catalog into an official SKU/spec source.
-- Sales price and source specs live here; internal unit cost stays in
-- vkpi_product_cost_catalog.

CREATE TABLE IF NOT EXISTS vkpi_products (
    sku TEXT PRIMARY KEY,
    category_main TEXT NOT NULL,
    category_detail TEXT,
    model_name TEXT NOT NULL,
    marketing_name TEXT,
    price_usd NUMERIC(10,2),
    status TEXT NOT NULL DEFAULT 'priced',
    description TEXT,
    source_file TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE vkpi_products ADD COLUMN IF NOT EXISTS series TEXT DEFAULT '';
ALTER TABLE vkpi_products ADD COLUMN IF NOT EXISTS mount TEXT DEFAULT '';
ALTER TABLE vkpi_products ADD COLUMN IF NOT EXISTS product_url TEXT DEFAULT '';
ALTER TABLE vkpi_products ADD COLUMN IF NOT EXISTS specs_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE vkpi_products ADD COLUMN IF NOT EXISTS fit_tags_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE vkpi_products ADD COLUMN IF NOT EXISTS source_url TEXT DEFAULT '';
ALTER TABLE vkpi_products ADD COLUMN IF NOT EXISTS source_checked_at TIMESTAMPTZ;
ALTER TABLE vkpi_products ADD COLUMN IF NOT EXISTS source_confidence NUMERIC(4,2) NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_products_category ON vkpi_products(category_main);
CREATE INDEX IF NOT EXISTS idx_products_status ON vkpi_products(status);
CREATE INDEX IF NOT EXISTS idx_products_price ON vkpi_products(price_usd) WHERE price_usd IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_series_mount ON vkpi_products(series, mount);
CREATE INDEX IF NOT EXISTS idx_products_source_confidence ON vkpi_products(source_confidence DESC, updated_at DESC);
