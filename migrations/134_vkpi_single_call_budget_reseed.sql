-- Re-seed the generic single-call hard-stop ceiling.
--
-- 077 seeded ('single_call', 0.50, ...) but the row is absent from the live
-- Postgres budget table, so video deep-analysis (which checks SINGLE_CALL_BUDGET_SCOPE
-- = "single_call") was hard-stopped by the require_configured gate even though
-- monthly_total / provider:gemini are far under cap. Re-seed idempotently so the
-- real per-call ceiling exists again. (The worker gate is also relaxed to the
-- enforce口径 require_configured=False so未配额 cost_scope 不再误杀。)
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
  ('single_call', 0.50, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0', '{"seeded_by":"134_vkpi_single_call_budget_reseed","tier":"hard_stop","note":"reseed_missing_077_row"}')
ON CONFLICT(scope) DO NOTHING;
