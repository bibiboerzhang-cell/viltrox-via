CREATE TABLE IF NOT EXISTS vkpi_kol_url_deep_crawl_runs (
  id BIGSERIAL PRIMARY KEY,
  kol_pool_id BIGINT NULL REFERENCES vkpi_kol_pool(id) ON DELETE SET NULL,
  source_url TEXT NOT NULL,
  url_type TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'dry_run',
  status TEXT NOT NULL DEFAULT 'planned',
  dry_run BOOLEAN NOT NULL DEFAULT TRUE,
  result_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_vkpi_kol_url_deep_crawl_runs_url_type
    CHECK (url_type IN ('profile', 'video', 'unknown')),
  CONSTRAINT chk_vkpi_kol_url_deep_crawl_runs_status
    CHECK (status IN ('planned', 'running', 'ready', 'failed', 'cancelled')),
  CONSTRAINT chk_vkpi_kol_url_deep_crawl_runs_mode
    CHECK (mode IN ('auto', 'profile_only', 'video_deep', 'dry_run'))
);

CREATE TABLE IF NOT EXISTS vkpi_kol_llm_deep_analysis_results (
  id BIGSERIAL PRIMARY KEY,
  kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
  source_url TEXT NOT NULL,
  source_evidence_id BIGINT NULL REFERENCES vkpi_kol_video_evidence(id) ON DELETE SET NULL,
  analysis_kind TEXT NOT NULL,
  llm_v6_fit NUMERIC(8, 3),
  llm_dimensions_11 JSONB NOT NULL DEFAULT '{}'::jsonb,
  method TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  confidence NUMERIC(8, 5),
  source_cache_id BIGINT NULL REFERENCES vkpi_analysis_cache(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'ready',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT chk_vkpi_kol_llm_deep_analysis_results_kind
    CHECK (analysis_kind IN ('video_final_v1', 'profile_llm', 'url_standalone')),
  CONSTRAINT chk_vkpi_kol_llm_deep_analysis_results_status
    CHECK (status IN ('planned', 'running', 'ready', 'failed', 'void')),
  CONSTRAINT chk_vkpi_kol_llm_deep_analysis_results_confidence
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_url_deep_crawl_runs_kol_created
  ON vkpi_kol_url_deep_crawl_runs(kol_pool_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_url_deep_crawl_runs_status_created
  ON vkpi_kol_url_deep_crawl_runs(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_llm_deep_analysis_results_kol_created
  ON vkpi_kol_llm_deep_analysis_results(kol_pool_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_llm_deep_analysis_results_kind_status
  ON vkpi_kol_llm_deep_analysis_results(analysis_kind, status);

COMMENT ON TABLE vkpi_kol_url_deep_crawl_runs IS
  'URL deep-crawl run ledger for profile/video URL workflows. It records orchestration state only.';

COMMENT ON TABLE vkpi_kol_llm_deep_analysis_results IS
  'Independent LLM deep analysis results for KOL URLs. llm_v6_fit is not viltrox_fit_score and must not drive KOL Pool default ranking.';

COMMENT ON COLUMN vkpi_kol_llm_deep_analysis_results.llm_v6_fit IS
  'LLM-only deep-fit signal for URL analysis. Independent from vkpi_kol_pool.viltrox_fit_score.';
