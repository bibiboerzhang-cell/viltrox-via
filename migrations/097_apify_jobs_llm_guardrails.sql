-- C4 analysis worker guardrails.
--
-- This only adds brake controls. It does not enqueue jobs, run providers, or
-- enable any LLM analyzer path by itself.

ALTER TABLE apify_jobs
  DROP CONSTRAINT IF EXISTS chk_apify_jobs_status;

ALTER TABLE apify_jobs
  ADD CONSTRAINT chk_apify_jobs_status
  CHECK (status IN ('queued', 'running', 'done', 'failed', 'blocked'));

INSERT INTO vkpi_provider_budget_caps (
  scope,
  cap_usd,
  current_spend,
  warning_at,
  hard_stop_at,
  reset_at,
  fallback_action,
  metadata_json
) VALUES
  (
    'cron:vkpi_analysis_worker',
    50.00,
    0,
    0.80,
    1.00,
    NULL,
    'block_job',
    '{"seeded_by":"097_apify_jobs_llm_guardrails","tier":"worker","package":"C","provider":"gemini","target_types":["video","contract"]}'
  )
ON CONFLICT(scope) DO NOTHING;
