-- V-KPI P2B legacy import additive patch.
-- Adds launch-plan staging and batch rollback policy fields without modifying
-- the already committed 058 migration.

ALTER TABLE vkpi_legacy_import_batches
  ADD COLUMN IF NOT EXISTS rollback_until TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS rollback_policy TEXT NOT NULL DEFAULT 'manual_30m',
  ADD COLUMN IF NOT EXISTS auto_rollback_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS vkpi_legacy_launch_plans_staging (
  id BIGSERIAL PRIMARY KEY,
  import_batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  row_uid TEXT NOT NULL UNIQUE,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  launch_name TEXT DEFAULT '',
  product_sku TEXT DEFAULT '',
  product_name TEXT DEFAULT '',
  category_primary TEXT DEFAULT '',
  category_secondary TEXT DEFAULT '',
  launch_date DATE,
  target_region TEXT DEFAULT '',
  target_platforms_json TEXT NOT NULL DEFAULT '[]',
  campaign_owner TEXT DEFAULT '',
  official_material_ref TEXT DEFAULT '',
  kol_plan_ref TEXT DEFAULT '',
  product_page_url TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'planned',
  notes TEXT DEFAULT '',
  review_status TEXT NOT NULL DEFAULT 'pending',
  review_reason_json TEXT NOT NULL DEFAULT '[]',
  import_action TEXT NOT NULL DEFAULT 'stage_only',
  row_hash TEXT DEFAULT '',
  raw_row_json TEXT NOT NULL DEFAULT '{}',
  validation_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(import_batch_id, source_sheet, source_row)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_launch_stage_batch
  ON vkpi_legacy_launch_plans_staging(import_batch_id, review_status);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_launch_stage_product
  ON vkpi_legacy_launch_plans_staging(product_name, launch_date);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_launch_stage_source
  ON vkpi_legacy_launch_plans_staging(source_sheet, source_row);
