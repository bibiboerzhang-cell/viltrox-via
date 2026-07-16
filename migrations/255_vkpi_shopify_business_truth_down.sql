-- Down migration only removes the additive proof schema. Historical financial
-- rows deliberately remain stale because their former truth cannot be restored.

DROP INDEX IF EXISTS idx_vkpi_shopify_snapshot_truth;
DROP INDEX IF EXISTS idx_vkpi_shopify_order_truth;

ALTER TABLE vkpi_shopify_order_snapshots
  DROP CONSTRAINT IF EXISTS chk_vkpi_shopify_native_proof;

ALTER TABLE vkpi_shopify_orders
  DROP CONSTRAINT IF EXISTS chk_vkpi_shopify_order_native_proof;

ALTER TABLE vkpi_shopify_orders DROP COLUMN IF EXISTS cancelled_at;
ALTER TABLE vkpi_shopify_orders DROP COLUMN IF EXISTS raw_payload_hash;
ALTER TABLE vkpi_shopify_orders DROP COLUMN IF EXISTS provider_verified_at;
ALTER TABLE vkpi_shopify_orders DROP COLUMN IF EXISTS provider_auth_mode;

ALTER TABLE vkpi_kpi_ledger DROP COLUMN IF EXISTS superseded_metric_value;

ALTER TABLE vkpi_shopify_order_snapshots DROP COLUMN IF EXISTS provider_verified_at;
ALTER TABLE vkpi_shopify_order_snapshots DROP COLUMN IF EXISTS provider_auth_mode;
ALTER TABLE vkpi_shopify_order_snapshots DROP COLUMN IF EXISTS cancelled_at;
