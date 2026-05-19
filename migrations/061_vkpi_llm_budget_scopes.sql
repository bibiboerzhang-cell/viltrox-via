-- P5 LLM budget scopes used by purpose-derived llm_gateway cost tags.

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
  ('cron:vkpi_weekly_report', 25.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule', '{"seeded_by":"061_vkpi_llm_budget_scopes","tier":"cron"}'),
  ('cron:vkpi_pillar', 15.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule', '{"seeded_by":"061_vkpi_llm_budget_scopes","tier":"cron"}'),
  ('cron:p4_recommendation_reasons', 30.00, 0, 0.80, 1.00, NULL, 'fallback_to_deterministic_reason', '{"seeded_by":"061_vkpi_llm_budget_scopes","tier":"cron"}')
ON CONFLICT(scope) DO NOTHING;
