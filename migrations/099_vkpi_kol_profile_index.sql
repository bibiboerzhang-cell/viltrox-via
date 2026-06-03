CREATE TABLE IF NOT EXISTS vkpi_kol_profile_index_runs (
  id BIGSERIAL PRIMARY KEY,
  run_uid TEXT NOT NULL UNIQUE,
  collection_name TEXT NOT NULL DEFAULT 'vkpi_kol_profile_index_v1',
  embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
  vector_size INTEGER NOT NULL DEFAULT 1536,
  method TEXT NOT NULL DEFAULT 'vector_recall',
  status TEXT NOT NULL DEFAULT 'planned',
  target_count INTEGER NOT NULL DEFAULT 0,
  embedded_count INTEGER NOT NULL DEFAULT 0,
  estimated_tokens INTEGER NOT NULL DEFAULT 0,
  estimated_cost_usd NUMERIC(14, 8) NOT NULL DEFAULT 0,
  actual_tokens INTEGER NOT NULL DEFAULT 0,
  actual_cost_usd NUMERIC(14, 8) NOT NULL DEFAULT 0,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_vkpi_kol_profile_index_runs_method
    CHECK (method = 'vector_recall'),
  CONSTRAINT chk_vkpi_kol_profile_index_runs_status
    CHECK (status IN ('planned', 'running', 'ready', 'failed', 'void'))
);

CREATE TABLE IF NOT EXISTS vkpi_kol_profile_index_entries (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NULL REFERENCES vkpi_kol_profile_index_runs(id) ON DELETE SET NULL,
  kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
  collection_name TEXT NOT NULL DEFAULT 'vkpi_kol_profile_index_v1',
  qdrant_point_id TEXT NOT NULL,
  embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
  vector_size INTEGER NOT NULL DEFAULT 1536,
  method TEXT NOT NULL DEFAULT 'vector_recall',
  profile_text_hash TEXT NOT NULL,
  profile_text TEXT NOT NULL,
  source_fields_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  sufficiency TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_vkpi_kol_profile_index_entries_method
    CHECK (method = 'vector_recall'),
  CONSTRAINT chk_vkpi_kol_profile_index_entries_sufficiency
    CHECK (sufficiency IN ('adequate', 'strong')),
  CONSTRAINT chk_vkpi_kol_profile_index_entries_status
    CHECK (status IN ('ready', 'stale', 'excluded'))
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_profile_index_runs_collection_created
  ON vkpi_kol_profile_index_runs (collection_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_profile_index_entries_kol
  ON vkpi_kol_profile_index_entries (kol_pool_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_profile_index_entries_collection_status
  ON vkpi_kol_profile_index_entries (collection_name, status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_kol_profile_index_entries_profile
  ON vkpi_kol_profile_index_entries (collection_name, kol_pool_id, embedding_model, profile_text_hash);

CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_kol_profile_index_entries_point
  ON vkpi_kol_profile_index_entries (collection_name, qdrant_point_id);

COMMENT ON TABLE vkpi_kol_profile_index_runs IS
  'KOL vector recall build runs. This index is only for vector_recall and must not feed V6 Fit scoring.';

COMMENT ON TABLE vkpi_kol_profile_index_entries IS
  'KOL profile text and Qdrant point metadata for vkpi_kol_profile_index_v1. Vector scores are returned live, not persisted as V6 Fit.';
