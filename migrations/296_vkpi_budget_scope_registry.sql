-- 296: Complete the reviewed feature-budget registry and repair legacy ratio metadata.
--
-- Scope selection is deliberately conservative: every ordinary row below has
-- both a current production call site and observed cost-ledger evidence.  The
-- three reviewed exception rows retain their previously approved caps.  This
-- migration never replaces an existing row, so operator-set caps, spend and
-- reset anchors remain authoritative.

-- metadata_json is a legacy TEXT column and historical operator rows may
-- contain malformed JSON.  Parse through a session-local fail-safe helper so
-- drift repair skips those rows instead of aborting the seed transaction.  A
-- NULL result is intentionally never written back: malformed operator data is
-- retained byte-for-byte for later manual reconciliation.
CREATE OR REPLACE FUNCTION pg_temp.vkpi_296_try_parse_jsonb(raw_text TEXT)
RETURNS JSONB
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $function$
DECLARE
    parsed JSONB;
BEGIN
    parsed := raw_text::jsonb;
    IF jsonb_typeof(parsed) <> 'object' THEN
        RETURN NULL;
    END IF;
    RETURN parsed;
EXCEPTION
    WHEN invalid_text_representation THEN
        RETURN NULL;
END;
$function$;

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
    ('kol_smart_search_query_plan', 10.00, 0, 0.80, 1.00,
     (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC',
     'fallback_to_rule_query_plan',
     '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"kol_smart_search_query_plan"}'),
    ('vkpi_intelligent_ask', 10.00, 0, 0.80, 1.00,
     (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC',
     'evidence_only_response',
     '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"vkpi_intelligent_ask"}'),
    ('vkpi_kol_outreach_draft', 10.00, 0, 0.80, 1.00,
     (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC',
     'skip_llm_keep_last',
     '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"vkpi_kol_outreach_draft"}'),
    ('vkpi_kol_outreach_optimize', 10.00, 0, 0.80, 1.00,
     (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC',
     'skip_llm_keep_input',
     '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"vkpi_kol_outreach_optimize"}'),
    ('vkpi_sentiment', 10.00, 0, 0.80, 1.00,
     (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC',
     'fallback_to_rule_v0',
     '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"vkpi_sentiment"}'),
    ('vkpi_pillar', 10.00, 0, 0.80, 1.00,
     (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC',
     'fallback_to_rule_v0',
     '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"vkpi_pillar"}'),
    ('cron:vkpi_weekly_summary', 2.00, 0, 0.80, 1.00,
     (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC',
     'fallback_to_template',
     '{"seeded_by":"migration_296","tier":"cron","window":"daily","cost_tag":"cron:vkpi_weekly_summary"}'),
    ('cron:vkpi_bio_translate', 2.00, 0, 0.80, 1.00,
     (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC',
     'keep_original_text',
     '{"seeded_by":"migration_296","tier":"cron","window":"daily","cost_tag":"cron:vkpi_bio_translate"}'),
    ('cron:kol_outreach_pack', 2.00, 0, 0.80, 1.00,
     (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC',
     'skip_llm_keep_last',
     '{"seeded_by":"migration_296","tier":"cron","window":"daily","cost_tag":"cron:kol_outreach_pack"}'),
    ('cron:gemini_video_legacy', 2.00, 0, 0.80, 1.00,
     (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC',
     'fallback_to_cached_analysis',
     '{"seeded_by":"migration_296","tier":"cron","window":"daily","cost_tag":"cron:gemini_video_legacy"}'),
    ('cron:marketing_advisor', 2.00, 0, 0.80, 1.00,
     (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC',
     'fallback_to_evidence_only',
     '{"seeded_by":"migration_296","tier":"advisor","window":"daily","cost_tag":"cron:marketing_advisor"}'),
    ('agent_skill', 40.00, 0, 0.80, 1.00,
     (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC',
     'rule_mode_dry_run',
     '{"seeded_by":"migration_296","tier":"agent","window":"monthly","package":"skill_auto_orchestrate"}'),
    ('metric_tracking', 30.00, 0, 0.80, 1.00,
     (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC',
     'pause_tracking_enqueue',
     '{"seeded_by":"migration_296","tier":"feature","window":"monthly","provider":"apify","cost_tag":"metric_tracking","cap_env":"VKPI_METRIC_TRACKING_MONTHLY_CAP_USD"}'),
    ('agent_alert_explain', 5.00, 0, 0.80, 1.00,
     (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC',
     'rule_explanation',
     '{"seeded_by":"migration_296","tier":"agent","window":"monthly","package":"anomaly_sentinel_explain"}')
ON CONFLICT (scope) DO NOTHING;

-- Migration 292 described both agent scopes as manual monthly windows.  Runtime
-- now rolls every ordinary feature scope monthly.  Repair only untouched 292
-- metadata and leave cap, spend and reset_at unchanged.
UPDATE vkpi_provider_budget_caps
SET metadata_json = (
    pg_temp.vkpi_296_try_parse_jsonb(metadata_json)
    || '{"window":"monthly","window_repaired_by":"migration_296","legacy_window":"manual_monthly"}'::jsonb
)::text
WHERE scope IN ('agent_skill', 'agent_alert_explain')
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'seeded_by' = 'migration_292'
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window' = 'manual_monthly';

-- Older metric-tracking rows were seeded by the enrollment command before the
-- shared monthly-window classifier existed.  Add description only when absent.
UPDATE vkpi_provider_budget_caps
SET metadata_json = (
    pg_temp.vkpi_296_try_parse_jsonb(metadata_json)
    || '{"window":"monthly","window_seeded_by":"migration_296"}'::jsonb
)::text
WHERE scope = 'metric_tracking'
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) IS NOT NULL
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window' IS NULL;

-- Migrations 153, 157 and 158 wrote absolute dollar thresholds into ratio
-- columns.  The runtime clamp made those warnings fire only at the hard stop.
-- Match both the original values and original seed marker so operator-reviewed
-- rows are never normalized merely because they share a scope name.
UPDATE vkpi_provider_budget_caps
SET warning_at = 0.80,
    hard_stop_at = 1.00,
    metadata_json = (
        pg_temp.vkpi_296_try_parse_jsonb(metadata_json)
        || jsonb_build_object(
            'thresholds_repaired_by', 'migration_296',
            'legacy_warning_at', warning_at::text,
            'legacy_hard_stop_at', hard_stop_at::text
        )
        || CASE
            WHEN pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'threshold_unit' IS NULL
            THEN '{"threshold_unit":"ratio","threshold_unit_seeded_by":"migration_296"}'::jsonb
            ELSE '{}'::jsonb
           END
        || CASE
            WHEN pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window' IS NULL
            THEN '{"window":"daily","window_seeded_by":"migration_296"}'::jsonb
            ELSE '{}'::jsonb
           END
    )::text
WHERE (
        scope = 'dashboard:report_analysis'
        AND warning_at = 2.40
        AND hard_stop_at = 3.00
        AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'seeded_by' = 'migration_153'
    ) OR (
        scope = 'cron:official_daily_report'
        AND warning_at = 3.20
        AND hard_stop_at = 4.00
        AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'seeded_by' = 'migration_157'
    ) OR (
        scope = 'cron:official_visual'
        AND warning_at = 6.40
        AND hard_stop_at = 8.00
        AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'seeded_by' = 'migration_158'
    );

DROP FUNCTION pg_temp.vkpi_296_try_parse_jsonb(TEXT);
