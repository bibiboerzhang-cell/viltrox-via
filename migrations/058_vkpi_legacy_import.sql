-- V-KPI P2B legacy Excel multi-staging import.
-- This migration creates staging, review, log, and rollback-reference tables
-- only. It does not write official KOL, project, content, cost, risk, or VOC
-- tables.

CREATE TABLE IF NOT EXISTS vkpi_legacy_import_batches (
  id BIGSERIAL PRIMARY KEY,
  batch_uid TEXT NOT NULL UNIQUE,
  source_file_name TEXT NOT NULL DEFAULT '',
  source_file_sha256 TEXT NOT NULL DEFAULT '',
  source_file_size_bytes BIGINT NOT NULL DEFAULT 0,
  source_workbook_path TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'uploaded',
  uploaded_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  parsed_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  committed_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  rolled_back_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  total_rows INTEGER NOT NULL DEFAULT 0,
  staging_rows INTEGER NOT NULL DEFAULT 0,
  review_rows INTEGER NOT NULL DEFAULT 0,
  committed_rows INTEGER NOT NULL DEFAULT 0,
  rolled_back_rows INTEGER NOT NULL DEFAULT 0,
  contains_pii BOOLEAN NOT NULL DEFAULT TRUE,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  parsed_at TIMESTAMPTZ,
  committed_at TIMESTAMPTZ,
  rolled_back_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_batches_status
  ON vkpi_legacy_import_batches(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_batches_file_sha
  ON vkpi_legacy_import_batches(source_file_sha256);

CREATE TABLE IF NOT EXISTS vkpi_legacy_kol_profiles_staging (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  row_uid TEXT NOT NULL UNIQUE,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  platform TEXT DEFAULT '',
  normalized_platform TEXT DEFAULT '',
  handle TEXT DEFAULT '',
  normalized_handle TEXT DEFAULT '',
  dedup_key TEXT DEFAULT '',
  display_name TEXT DEFAULT '',
  country TEXT DEFAULT '',
  region TEXT DEFAULT '',
  category TEXT DEFAULT '',
  email TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  address TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  contact_missing BOOLEAN NOT NULL DEFAULT FALSE,
  contact_visibility_level TEXT NOT NULL DEFAULT 'restricted',
  contains_pii BOOLEAN NOT NULL DEFAULT TRUE,
  duplicate_in_batch BOOLEAN NOT NULL DEFAULT FALSE,
  review_status TEXT NOT NULL DEFAULT 'pending',
  review_reason_json TEXT NOT NULL DEFAULT '[]',
  matched_kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
  matched_kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
  import_action TEXT NOT NULL DEFAULT 'stage_only',
  row_hash TEXT DEFAULT '',
  raw_row_json TEXT NOT NULL DEFAULT '{}',
  validation_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(batch_id, source_sheet, source_row)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_kol_stage_batch
  ON vkpi_legacy_kol_profiles_staging(batch_id, review_status);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_kol_stage_dedup
  ON vkpi_legacy_kol_profiles_staging(dedup_key);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_kol_stage_source
  ON vkpi_legacy_kol_profiles_staging(source_sheet, source_row);

CREATE TABLE IF NOT EXISTS vkpi_legacy_cooperations_staging (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  row_uid TEXT NOT NULL UNIQUE,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  kol_staging_id BIGINT REFERENCES vkpi_legacy_kol_profiles_staging(id) ON DELETE SET NULL,
  matched_kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
  matched_kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
  platform TEXT DEFAULT '',
  normalized_platform TEXT DEFAULT '',
  handle TEXT DEFAULT '',
  normalized_handle TEXT DEFAULT '',
  dedup_key TEXT DEFAULT '',
  display_name TEXT DEFAULT '',
  product TEXT DEFAULT '',
  project TEXT DEFAULT '',
  status TEXT DEFAULT '',
  cooperation_date DATE,
  cost_amount NUMERIC(12,2),
  cost_currency TEXT DEFAULT '',
  content_link TEXT DEFAULT '',
  result TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  unmatched_kol_review BOOLEAN NOT NULL DEFAULT FALSE,
  review_status TEXT NOT NULL DEFAULT 'pending',
  review_reason_json TEXT NOT NULL DEFAULT '[]',
  import_action TEXT NOT NULL DEFAULT 'stage_only',
  row_hash TEXT DEFAULT '',
  raw_row_json TEXT NOT NULL DEFAULT '{}',
  validation_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(batch_id, source_sheet, source_row)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_coop_stage_batch
  ON vkpi_legacy_cooperations_staging(batch_id, review_status);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_coop_stage_dedup
  ON vkpi_legacy_cooperations_staging(dedup_key);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_coop_stage_product
  ON vkpi_legacy_cooperations_staging(product, status);

CREATE TABLE IF NOT EXISTS vkpi_legacy_official_content_staging (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  row_uid TEXT NOT NULL UNIQUE,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  official_account TEXT DEFAULT '',
  platform TEXT DEFAULT '',
  normalized_platform TEXT DEFAULT '',
  publish_date TIMESTAMPTZ,
  content_type TEXT DEFAULT '',
  title TEXT DEFAULT '',
  product TEXT DEFAULT '',
  link TEXT DEFAULT '',
  status TEXT DEFAULT '',
  owner TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  review_status TEXT NOT NULL DEFAULT 'pending',
  review_reason_json TEXT NOT NULL DEFAULT '[]',
  import_action TEXT NOT NULL DEFAULT 'stage_only',
  row_hash TEXT DEFAULT '',
  raw_row_json TEXT NOT NULL DEFAULT '{}',
  validation_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(batch_id, source_sheet, source_row)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_official_stage_batch
  ON vkpi_legacy_official_content_staging(batch_id, review_status);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_official_stage_account
  ON vkpi_legacy_official_content_staging(normalized_platform, official_account);

CREATE TABLE IF NOT EXISTS vkpi_legacy_product_costs_staging (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  row_uid TEXT NOT NULL UNIQUE,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  sku TEXT DEFAULT '',
  product_name TEXT DEFAULT '',
  cost NUMERIC(12,2),
  currency TEXT DEFAULT 'CNY',
  region TEXT DEFAULT '',
  effective_date DATE,
  notes TEXT DEFAULT '',
  review_status TEXT NOT NULL DEFAULT 'pending',
  review_reason_json TEXT NOT NULL DEFAULT '[]',
  import_action TEXT NOT NULL DEFAULT 'stage_only',
  row_hash TEXT DEFAULT '',
  raw_row_json TEXT NOT NULL DEFAULT '{}',
  validation_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(batch_id, source_sheet, source_row)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_product_cost_stage_batch
  ON vkpi_legacy_product_costs_staging(batch_id, review_status);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_product_cost_stage_sku
  ON vkpi_legacy_product_costs_staging(sku, region, effective_date);

CREATE TABLE IF NOT EXISTS vkpi_legacy_risk_watchlist_staging (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  row_uid TEXT NOT NULL UNIQUE,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  matched_kol_id BIGINT REFERENCES kols(id) ON DELETE SET NULL,
  matched_kol_pool_id BIGINT REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
  platform TEXT DEFAULT '',
  normalized_platform TEXT DEFAULT '',
  handle TEXT DEFAULT '',
  normalized_handle TEXT DEFAULT '',
  dedup_key TEXT DEFAULT '',
  display_name TEXT DEFAULT '',
  risk_type TEXT DEFAULT '',
  risk_reason TEXT DEFAULT '',
  severity TEXT NOT NULL DEFAULT 'medium',
  evidence TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  notes TEXT DEFAULT '',
  risk_only BOOLEAN NOT NULL DEFAULT TRUE,
  review_status TEXT NOT NULL DEFAULT 'pending',
  review_reason_json TEXT NOT NULL DEFAULT '[]',
  import_action TEXT NOT NULL DEFAULT 'stage_only',
  row_hash TEXT DEFAULT '',
  raw_row_json TEXT NOT NULL DEFAULT '{}',
  validation_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(batch_id, source_sheet, source_row)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_risk_stage_batch
  ON vkpi_legacy_risk_watchlist_staging(batch_id, review_status);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_risk_stage_dedup
  ON vkpi_legacy_risk_watchlist_staging(dedup_key, severity);

CREATE TABLE IF NOT EXISTS vkpi_legacy_voc_alerts_staging (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  row_uid TEXT NOT NULL UNIQUE,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  platform TEXT DEFAULT '',
  normalized_platform TEXT DEFAULT '',
  product TEXT DEFAULT '',
  issue_type TEXT DEFAULT '',
  sentiment TEXT DEFAULT '',
  content TEXT DEFAULT '',
  link TEXT DEFAULT '',
  evidence TEXT DEFAULT '',
  issue_date DATE,
  severity TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'open',
  owner TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  review_status TEXT NOT NULL DEFAULT 'pending',
  review_reason_json TEXT NOT NULL DEFAULT '[]',
  import_action TEXT NOT NULL DEFAULT 'stage_only',
  row_hash TEXT DEFAULT '',
  raw_row_json TEXT NOT NULL DEFAULT '{}',
  validation_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(batch_id, source_sheet, source_row)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_voc_stage_batch
  ON vkpi_legacy_voc_alerts_staging(batch_id, review_status);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_voc_stage_product
  ON vkpi_legacy_voc_alerts_staging(product, issue_type, severity);

CREATE TABLE IF NOT EXISTS vkpi_legacy_import_review_queue (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  pipeline TEXT NOT NULL,
  staging_table TEXT NOT NULL,
  staging_id BIGINT,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  review_type TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'open',
  assigned_to_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  resolved_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  resolution_action TEXT DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  resolution_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_review_batch
  ON vkpi_legacy_import_review_queue(batch_id, status, severity);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_review_source
  ON vkpi_legacy_import_review_queue(source_sheet, source_row);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_review_pipeline
  ON vkpi_legacy_import_review_queue(pipeline, review_type, status);

CREATE TABLE IF NOT EXISTS vkpi_legacy_import_logs (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  actor_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ok',
  detail TEXT DEFAULT '',
  row_count INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_logs_batch
  ON vkpi_legacy_import_logs(batch_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_logs_action
  ON vkpi_legacy_import_logs(action, status, occurred_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_legacy_import_committed_refs (
  id BIGSERIAL PRIMARY KEY,
  batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  pipeline TEXT NOT NULL,
  staging_table TEXT NOT NULL,
  staging_id BIGINT NOT NULL,
  target_table TEXT NOT NULL,
  target_id TEXT NOT NULL,
  commit_action TEXT NOT NULL,
  previous_snapshot_json TEXT NOT NULL DEFAULT '{}',
  new_snapshot_json TEXT NOT NULL DEFAULT '{}',
  rollback_status TEXT NOT NULL DEFAULT 'not_rolled_back',
  committed_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  rolled_back_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
  committed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  rolled_back_at TIMESTAMPTZ,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(batch_id, pipeline, staging_table, staging_id, target_table, target_id)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_committed_batch
  ON vkpi_legacy_import_committed_refs(batch_id, rollback_status);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_committed_target
  ON vkpi_legacy_import_committed_refs(target_table, target_id);
