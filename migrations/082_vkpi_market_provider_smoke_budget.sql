-- V2 market provider smoke budget scope.
--
-- This only permits a bounded manual single-call smoke after all other LLM
-- gateway hard caps pass. It does not schedule daily scans or enable batch LLM.

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
    'cron:market_provider_smoke',
    1.00,
    0,
    0.80,
    1.00,
    NULL,
    'fallback_to_preflight_only',
    '{"seeded_by":"082_vkpi_market_provider_smoke_budget","tier":"cron","package":"market_intelligence","provider":"llm","mode":"manual_single_smoke"}'
  )
ON CONFLICT(scope) DO NOTHING;
