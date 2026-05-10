-- migrations/054_vkpi_comment_intelligence_runs.sql
-- P2.2: pipeline run history / status / retry

CREATE TABLE IF NOT EXISTS vkpi_comment_intelligence_runs (
  id BIGSERIAL PRIMARY KEY,
  run_uid TEXT UNIQUE NOT NULL,
  post_id BIGINT NOT NULL,
  post_table VARCHAR(50) NOT NULL DEFAULT 'industry_posts',
  status VARCHAR(20) NOT NULL DEFAULT 'running',
  triggered_by VARCHAR(50),
  staff_id INT,
  retry_of_run_id BIGINT,
  params_json TEXT,
  steps_json TEXT,
  error_message TEXT,
  started_at TIMESTAMPTZ DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_ci_runs_post
  ON vkpi_comment_intelligence_runs(post_id, post_table, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_ci_runs_status
  ON vkpi_comment_intelligence_runs(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vkpi_ci_runs_retry
  ON vkpi_comment_intelligence_runs(retry_of_run_id);

