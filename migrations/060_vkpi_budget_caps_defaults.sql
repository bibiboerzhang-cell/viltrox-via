-- Default provider and cron budget caps for P4 dry-run readiness.
--
-- These rows are guardrail configuration only. They do not record spend and do
-- not enable provider calls; P4 still starts with provider_calls_allowed=false.

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
  ('monthly_total', 300.00, 0, 0.80, 1.00, NULL, 'dry_run_only', '{"seeded_by":"060_vkpi_budget_caps_defaults","tier":"global"}'),
  ('provider:openai', 100.00, 0, 0.80, 1.00, NULL, 'skip_provider_call', '{"seeded_by":"060_vkpi_budget_caps_defaults","tier":"provider"}'),
  ('provider:claude', 100.00, 0, 0.80, 1.00, NULL, 'skip_provider_call', '{"seeded_by":"060_vkpi_budget_caps_defaults","tier":"provider"}'),
  ('provider:gemini', 60.00, 0, 0.80, 1.00, NULL, 'skip_provider_call', '{"seeded_by":"060_vkpi_budget_caps_defaults","tier":"provider"}'),
  ('provider:apify', 40.00, 0, 0.80, 1.00, NULL, 'skip_provider_call', '{"seeded_by":"060_vkpi_budget_caps_defaults","tier":"provider"}'),
  ('cron:p4_recommendations_daily', 50.00, 0, 0.80, 1.00, NULL, 'queue_for_manual_review', '{"seeded_by":"060_vkpi_budget_caps_defaults","tier":"cron"}'),
  ('cron:p4_market_signals_refresh', 25.00, 0, 0.80, 1.00, NULL, 'queue_for_manual_review', '{"seeded_by":"060_vkpi_budget_caps_defaults","tier":"cron"}')
ON CONFLICT(scope) DO NOTHING;
