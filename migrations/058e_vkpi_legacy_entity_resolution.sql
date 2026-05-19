-- V-KPI P2C legacy KOL entity resolution.
-- Builds canonical candidates from P2B staging without writing official KOL tables.

CREATE TABLE IF NOT EXISTS vkpi_legacy_resolution_runs (
  id BIGSERIAL PRIMARY KEY,
  import_batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  run_uid TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'running',
  entity_count INTEGER NOT NULL DEFAULT 0,
  ref_count INTEGER NOT NULL DEFAULT 0,
  ready_count INTEGER NOT NULL DEFAULT 0,
  review_count INTEGER NOT NULL DEFAULT 0,
  blocked_count INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_resolution_runs_batch
  ON vkpi_legacy_resolution_runs(import_batch_id, created_at DESC);

CREATE TABLE IF NOT EXISTS vkpi_legacy_kol_entities (
  id BIGSERIAL PRIMARY KEY,
  import_batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  run_id BIGINT REFERENCES vkpi_legacy_resolution_runs(id) ON DELETE SET NULL,
  entity_uid TEXT NOT NULL UNIQUE,
  canonical_key TEXT NOT NULL,
  normalized_platform TEXT NOT NULL DEFAULT '',
  normalized_handle TEXT NOT NULL DEFAULT '',
  display_name TEXT DEFAULT '',
  profile_url TEXT DEFAULT '',
  country TEXT DEFAULT '',
  region TEXT DEFAULT '',
  category TEXT DEFAULT '',
  email TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  contact_status TEXT NOT NULL DEFAULT 'unknown',
  contact_visibility_level TEXT NOT NULL DEFAULT 'restricted',
  confidence_score NUMERIC(5,4) NOT NULL DEFAULT 0,
  weak_label TEXT NOT NULL DEFAULT 'review',
  resolution_status TEXT NOT NULL DEFAULT 'candidate',
  evidence_count INTEGER NOT NULL DEFAULT 0,
  kol_profile_rows INTEGER NOT NULL DEFAULT 0,
  cooperation_rows INTEGER NOT NULL DEFAULT 0,
  risk_rows INTEGER NOT NULL DEFAULT 0,
  review_reason_json TEXT NOT NULL DEFAULT '[]',
  identity_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(import_batch_id, canonical_key)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_kol_entities_batch
  ON vkpi_legacy_kol_entities(import_batch_id, weak_label, confidence_score DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_kol_entities_key
  ON vkpi_legacy_kol_entities(canonical_key);

CREATE TABLE IF NOT EXISTS vkpi_legacy_kol_entity_refs (
  id BIGSERIAL PRIMARY KEY,
  import_batch_id BIGINT NOT NULL REFERENCES vkpi_legacy_import_batches(id) ON DELETE CASCADE,
  entity_id BIGINT NOT NULL REFERENCES vkpi_legacy_kol_entities(id) ON DELETE CASCADE,
  pipeline TEXT NOT NULL,
  staging_table TEXT NOT NULL,
  staging_id BIGINT NOT NULL,
  source_sheet TEXT NOT NULL,
  source_row INTEGER NOT NULL,
  match_key TEXT NOT NULL,
  match_method TEXT NOT NULL DEFAULT 'dedup_key',
  confidence_score NUMERIC(5,4) NOT NULL DEFAULT 0,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(import_batch_id, pipeline, staging_table, staging_id)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_entity_refs_entity
  ON vkpi_legacy_kol_entity_refs(entity_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_legacy_entity_refs_batch_pipeline
  ON vkpi_legacy_kol_entity_refs(import_batch_id, pipeline);
