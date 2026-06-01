CREATE TABLE IF NOT EXISTS vkpi_analysis_cache (
  id BIGSERIAL PRIMARY KEY,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  model TEXT,
  derive_method TEXT NOT NULL,
  result JSONB,
  cost NUMERIC,
  status TEXT NOT NULL DEFAULT 'ready',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  embedding_json JSONB NULL,
  triggered_by_user_id BIGINT NULL,
  CONSTRAINT chk_vkpi_analysis_cache_status
    CHECK (status IN ('ready', 'stale')),
  CONSTRAINT uq_vkpi_analysis_cache_target_method
    UNIQUE (target_type, target_id, derive_method)
);

CREATE INDEX IF NOT EXISTS idx_vkpi_analysis_cache_target
  ON vkpi_analysis_cache (target_type, target_id);

COMMENT ON TABLE vkpi_analysis_cache IS
  'Unified video/contract analysis result cache. Single-table design; vector placeholder reserved for future semantic retrieval.';

COMMENT ON COLUMN vkpi_analysis_cache.derive_method IS
  'Result source such as mock or gemini; mock results must be labeled mock and never presented as real analysis.';

COMMENT ON COLUMN vkpi_analysis_cache.triggered_by_user_id IS
  'Reserved for future multi-role platform use: records the user who triggered analysis for user/role traceability and rate limiting.';

COMMENT ON COLUMN vkpi_analysis_cache.embedding_json IS
  'JSONB placeholder because pgvector is not installed; future migration can replace this with embedding vector(768).';
