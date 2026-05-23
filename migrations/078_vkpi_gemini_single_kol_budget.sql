-- P4.55 Gemini single-KOL readiness budget scope.
--
-- This row does not enable provider calls. It only makes the later explicit
-- single-KOL paid test auditable under the LLM gateway hard gates.

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
  ('cron:p4_gemini_single_kol', 3.00, 0, 0.80, 1.00, NULL, 'fallback_to_preflight_only', '{"seeded_by":"078_vkpi_gemini_single_kol_budget","tier":"cron","package":"P4","provider":"gemini"}')
ON CONFLICT(scope) DO NOTHING;
