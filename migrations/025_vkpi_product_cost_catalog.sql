-- V-KPI product cost catalog.
-- Management owns lens/sample unit cost. Operators only enter shipping and
-- promotion fees; the product cost is posted automatically when a project is shipped.

CREATE TABLE IF NOT EXISTS vkpi_product_cost_catalog (
    id BIGSERIAL PRIMARY KEY,
    product_sku TEXT NOT NULL UNIQUE,
    product_name TEXT DEFAULT '',
    unit_cost_cents BIGINT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    note TEXT DEFAULT '',
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    updated_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_product_cost_catalog_active
    ON vkpi_product_cost_catalog(active, product_sku);
