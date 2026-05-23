-- P4 LLM gateway hard caps.
--
-- These rows do not enable provider calls. They add the single-call hard stop
-- required before any later LLM/Gemini live test can proceed.

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
  ('single_call', 0.50, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0', '{"seeded_by":"077_vkpi_llm_gateway_hard_caps","tier":"hard_stop","package":"P4"}'),
  ('cron:p4_evidence_summary', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_evidence_only', '{"seeded_by":"077_vkpi_llm_gateway_hard_caps","tier":"cron","package":"P4"}')
ON CONFLICT(scope) DO NOTHING;
