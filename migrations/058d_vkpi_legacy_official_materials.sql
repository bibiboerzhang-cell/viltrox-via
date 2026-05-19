-- V-KPI P2B legacy import official materials staging.
-- Captures the old "官方物料排期表" without writing official content tables.

CREATE TABLE IF NOT EXISTS vkpi_legacy_official_materials_staging (
  id BIGSERIAL PRIMARY KEY,
  import_batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  row_uid TEXT NOT NULL UNIQUE,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  launch_ref TEXT DEFAULT '',
  product_sku TEXT DEFAULT '',
  product_name TEXT DEFAULT '',
  owner TEXT DEFAULT '',
  production_status TEXT DEFAULT '',
  project TEXT DEFAULT '',
  content_type TEXT DEFAULT '',
  content_description TEXT DEFAULT '',
  reference_doc TEXT DEFAULT '',
  content_format TEXT DEFAULT '',
  request_date DATE,
  target_delivery_date DATE,
  asset_link TEXT DEFAULT '',
  size_spec TEXT DEFAULT '',
  publish_status TEXT DEFAULT '',
  product_publish_date DATE,
  production_team TEXT DEFAULT '',
  budget_amount NUMERIC(12,2),
  budget_currency TEXT DEFAULT '',
  official_usage_ref TEXT DEFAULT '',
  parent_ref TEXT DEFAULT '',
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

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_materials_stage_batch
  ON vkpi_legacy_official_materials_staging(import_batch_id, review_status);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_materials_stage_product
  ON vkpi_legacy_official_materials_staging(product_name, product_publish_date);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_materials_stage_usage
  ON vkpi_legacy_official_materials_staging(official_usage_ref);
