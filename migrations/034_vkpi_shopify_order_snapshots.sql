-- V-KPI Shopify order snapshots for evidence drilldown.

CREATE TABLE IF NOT EXISTS vkpi_shopify_order_snapshots (
    id BIGSERIAL PRIMARY KEY,
    shopify_order_id TEXT NOT NULL UNIQUE,
    admin_graphql_api_id TEXT DEFAULT '',
    order_name TEXT DEFAULT '',
    order_number TEXT DEFAULT '',
    processed_at TIMESTAMPTZ,
    currency TEXT DEFAULT 'USD',
    subtotal_cents BIGINT NOT NULL DEFAULT 0,
    total_cents BIGINT NOT NULL DEFAULT 0,
    financial_status TEXT DEFAULT '',
    fulfillment_status TEXT DEFAULT '',
    refund_status TEXT DEFAULT '',
    discount_codes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    landing_site TEXT DEFAULT '',
    note_attributes_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    line_items_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_payload_hash TEXT DEFAULT '',
    raw_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_shopify_order_processed
    ON vkpi_shopify_order_snapshots(processed_at DESC);

ALTER TABLE vkpi_sales_attributions
    ADD COLUMN IF NOT EXISTS shopify_order_snapshot_id BIGINT REFERENCES vkpi_shopify_order_snapshots(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_vkpi_sales_shopify_snapshot
    ON vkpi_sales_attributions(shopify_order_snapshot_id);
