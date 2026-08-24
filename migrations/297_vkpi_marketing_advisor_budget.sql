-- 297: Repair environments that applied migration 296 before the Marketing
-- Advisor's dedicated budget row was added to that migration file.
--
-- Existing operator-owned rows always win.  The Advisor remains separately
-- gated by VKPI_ADVISOR_EXTERNAL_AI_ENABLED and per-message explicit opt-in.

INSERT INTO vkpi_provider_budget_caps (
    scope,
    cap_usd,
    current_spend,
    warning_at,
    hard_stop_at,
    reset_at,
    fallback_action,
    metadata_json
) VALUES (
    'cron:marketing_advisor',
    2.00,
    0,
    0.80,
    1.00,
    (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC',
    'fallback_to_evidence_only',
    '{"seeded_by":"migration_297","tier":"advisor","window":"daily","cost_tag":"cron:marketing_advisor"}'
)
ON CONFLICT (scope) DO NOTHING;
