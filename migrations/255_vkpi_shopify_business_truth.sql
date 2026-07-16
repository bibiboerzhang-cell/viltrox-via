-- 255: Native Shopify proof and financial-materialization truth boundary.
-- Existing snapshots cannot be retroactively HMAC-verified because the
-- original signature header was not stored. They therefore remain legacy
-- evidence and all historical financial materializations must be recomputed.
-- The migration runner owns the transaction boundary.

ALTER TABLE vkpi_shopify_order_snapshots
  ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

ALTER TABLE vkpi_shopify_order_snapshots
  ADD COLUMN IF NOT EXISTS provider_auth_mode TEXT NOT NULL DEFAULT 'legacy_unverified';

ALTER TABLE vkpi_shopify_order_snapshots
  ADD COLUMN IF NOT EXISTS provider_verified_at TIMESTAMPTZ;

ALTER TABLE vkpi_shopify_orders
  ADD COLUMN IF NOT EXISTS provider_auth_mode TEXT NOT NULL DEFAULT 'legacy_unverified';

ALTER TABLE vkpi_shopify_orders
  ADD COLUMN IF NOT EXISTS provider_verified_at TIMESTAMPTZ;

ALTER TABLE vkpi_shopify_orders
  ADD COLUMN IF NOT EXISTS raw_payload_hash TEXT NOT NULL DEFAULT '';

ALTER TABLE vkpi_shopify_orders
  ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

UPDATE vkpi_shopify_order_snapshots
SET provider_auth_mode='legacy_unverified',
    provider_verified_at=NULL
WHERE provider_verified_at IS NULL
   OR provider_auth_mode <> 'shopify-hmac';

-- Fail closed for prior rows. A new native-HMAC event will upsert the snapshot
-- and attribution back to confirmed only when its financial state is eligible.
UPDATE vkpi_sales_attributions sa
SET confidence='unmatched'
WHERE sa.source_platform='shopify'
  AND sa.confidence IN ('confirmed','refund')
  AND NOT EXISTS (
    SELECT 1
    FROM vkpi_shopify_order_snapshots os
    WHERE os.id=sa.shopify_order_snapshot_id
      AND os.provider_auth_mode='shopify-hmac'
      AND os.provider_verified_at IS NOT NULL
      AND LOWER(COALESCE(os.financial_status,'')) IN ('paid','partially_paid','partially_refunded')
      AND os.cancelled_at IS NULL
  );

-- Old lineage values were computed under a looser source predicate. Preserve
-- their source maps, but remove their numeric claim until a fresh run rebuilds
-- them with migration 255's truth contract.
UPDATE vkpi_metric_values
SET value_numeric=NULL,
    data_status='unavailable',
    confidence=0,
    is_partial=TRUE
WHERE metric_key IN ('gmv','cost','net_contribution','roi');

-- KPI rows are also materialized facts. Keep the row and original value in a
-- superseded column for audit, but zero it and mark it stale so legacy readers cannot
-- continue presenting it as actual while the normal rollup rebuilds the day.
ALTER TABLE vkpi_kpi_ledger
  ADD COLUMN IF NOT EXISTS superseded_metric_value NUMERIC(18,4);

UPDATE vkpi_kpi_ledger
SET superseded_metric_value=metric_value,
    metric_value=0,
    confidence='stale'
WHERE metric_key IN (
  'revenue_cents','estimated_revenue_cents','cost_cents','net_contribution_cents','roi','net_roi',
  'workload_score','kpi_credit','recommendation_order_attributed',
  'recommendation_gmv_cents','recommendation_cost_cents','recommendation_roi'
);

CREATE INDEX IF NOT EXISTS idx_vkpi_shopify_snapshot_truth
  ON vkpi_shopify_order_snapshots(
    provider_auth_mode, financial_status, cancelled_at, provider_verified_at
  );

CREATE INDEX IF NOT EXISTS idx_vkpi_shopify_order_truth
  ON vkpi_shopify_orders(
    provider_auth_mode, financial_status, cancelled_at, provider_verified_at
  );

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_vkpi_shopify_native_proof'
  ) THEN
    ALTER TABLE vkpi_shopify_order_snapshots
      ADD CONSTRAINT chk_vkpi_shopify_native_proof
      CHECK (
        provider_verified_at IS NULL
        OR (
          provider_auth_mode='shopify-hmac'
          AND NULLIF(BTRIM(raw_payload_hash),'') IS NOT NULL
        )
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='chk_vkpi_shopify_order_native_proof'
  ) THEN
    ALTER TABLE vkpi_shopify_orders
      ADD CONSTRAINT chk_vkpi_shopify_order_native_proof
      CHECK (
        provider_verified_at IS NULL
        OR (
          provider_auth_mode='shopify-hmac'
          AND NULLIF(BTRIM(raw_payload_hash),'') IS NOT NULL
        )
      );
  END IF;
END $$;
