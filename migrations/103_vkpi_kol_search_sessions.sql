CREATE TABLE IF NOT EXISTS vkpi_kol_search_sessions (
  id BIGSERIAL PRIMARY KEY,
  query_text TEXT NOT NULL DEFAULT '',
  query_type TEXT NOT NULL DEFAULT 'unknown',
  source TEXT NOT NULL DEFAULT 'smart_kol_input',
  status TEXT NOT NULL DEFAULT 'planned',
  created_by BIGINT NULL,
  input_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  result_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_vkpi_kol_search_sessions_query_type
    CHECK (query_type IN ('url_video', 'url_profile', 'text_recall', 'unknown')),
  CONSTRAINT chk_vkpi_kol_search_sessions_status
    CHECK (status IN ('planned', 'running', 'ready', 'partial', 'failed', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS vkpi_kol_search_session_items (
  id BIGSERIAL PRIMARY KEY,
  session_id BIGINT NOT NULL REFERENCES vkpi_kol_search_sessions(id) ON DELETE CASCADE,
  dedupe_key TEXT NOT NULL,
  item_type TEXT NOT NULL DEFAULT 'unknown',
  status TEXT NOT NULL DEFAULT 'planned',
  stage TEXT NOT NULL DEFAULT 'identified',
  rank INTEGER NULL,
  score NUMERIC(12, 6) NULL,
  kol_pool_id BIGINT NULL REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
  evidence_id BIGINT NULL REFERENCES vkpi_kol_video_evidence(id) ON DELETE SET NULL,
  job_id BIGINT NULL REFERENCES apify_jobs(id) ON DELETE SET NULL,
  source_url TEXT NOT NULL DEFAULT '',
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_vkpi_kol_search_session_items_dedupe
    UNIQUE (session_id, dedupe_key),
  CONSTRAINT chk_vkpi_kol_search_session_items_type
    CHECK (item_type IN ('url_video', 'url_profile', 'recall_candidate', 'existing_kol', 'new_creator', 'unknown')),
  CONSTRAINT chk_vkpi_kol_search_session_items_status
    CHECK (status IN ('planned', 'identified', 'matched', 'queued', 'running', 'ready', 'partial', 'failed', 'skipped', 'already_queued', 'already_analyzed', 'unknown')),
  CONSTRAINT chk_vkpi_kol_search_session_items_stage
    CHECK (stage IN ('identified', 'profile', 'evidence', 'analysis', 'summary'))
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_search_sessions_created
  ON vkpi_kol_search_sessions(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_search_sessions_status_created
  ON vkpi_kol_search_sessions(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_search_session_items_session_rank
  ON vkpi_kol_search_session_items(session_id, rank NULLS LAST, id);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_search_session_items_kol
  ON vkpi_kol_search_session_items(kol_pool_id, updated_at DESC)
  WHERE kol_pool_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_search_session_items_job
  ON vkpi_kol_search_session_items(job_id)
  WHERE job_id IS NOT NULL;

COMMENT ON TABLE vkpi_kol_search_sessions IS
  'Unified KOL Pool search sessions for smart URL/profile/text recall workflows. Stores orchestration state only.';

COMMENT ON TABLE vkpi_kol_search_session_items IS
  'Candidates and URL-derived items belonging to one KOL search session. Does not drive V6 Fit scoring.';
