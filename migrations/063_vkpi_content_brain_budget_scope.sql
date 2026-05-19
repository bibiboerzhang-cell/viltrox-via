-- Budget scope for P6 content brain analysis.
--
-- This seeds guardrail configuration only. It does not enable provider calls;
-- P6-2 remains deterministic dry-run until a later package explicitly adds
-- budget-gated provider analysis.

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
  ('cron:p6_content_brain_analysis', 20.00, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0', '{"seeded_by":"063_vkpi_content_brain_budget_scope","tier":"cron","package":"P6"}')
ON CONFLICT(scope) DO NOTHING;
