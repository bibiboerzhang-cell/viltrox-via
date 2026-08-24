-- Roll back 296 conservatively.  Rows edited by operators or carrying spend are
-- retained; only untouched migration-296 defaults are deleted.

-- Match the forward migration's fail-safe JSON boundary.  Malformed historical
-- metadata must never turn rollback into an all-or-nothing outage and must not
-- be normalized, deleted, or otherwise rewritten by this migration.
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

-- Restore the three legacy threshold pairs only while the repaired pair and
-- repair marker are both intact.  Any later operator threshold edit wins.
UPDATE vkpi_provider_budget_caps
SET warning_at = CASE scope
        WHEN 'dashboard:report_analysis' THEN 2.40
        WHEN 'cron:official_daily_report' THEN 3.20
        WHEN 'cron:official_visual' THEN 6.40
        ELSE warning_at
    END,
    hard_stop_at = CASE scope
        WHEN 'dashboard:report_analysis' THEN 3.00
        WHEN 'cron:official_daily_report' THEN 4.00
        WHEN 'cron:official_visual' THEN 8.00
        ELSE hard_stop_at
    END
WHERE scope IN (
        'dashboard:report_analysis',
        'cron:official_daily_report',
        'cron:official_visual'
    )
  AND warning_at = 0.80
  AND hard_stop_at = 1.00
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'thresholds_repaired_by' = 'migration_296'
  AND (
        (scope = 'dashboard:report_analysis'
         AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'seeded_by' = 'migration_153'
         AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'legacy_warning_at' = '2.40'
         AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'legacy_hard_stop_at' = '3.00')
     OR (scope = 'cron:official_daily_report'
         AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'seeded_by' = 'migration_157'
         AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'legacy_warning_at' = '3.20'
         AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'legacy_hard_stop_at' = '4.00')
     OR (scope = 'cron:official_visual'
         AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'seeded_by' = 'migration_158'
         AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'legacy_warning_at' = '6.40'
         AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'legacy_hard_stop_at' = '8.00')
  );

-- Remove only descriptive fields that 296 itself added.  If an operator changed
-- either field after the migration, keep the operator value and only discard the
-- migration marker.
UPDATE vkpi_provider_budget_caps
SET metadata_json = (pg_temp.vkpi_296_try_parse_jsonb(metadata_json) - 'threshold_unit' - 'threshold_unit_seeded_by')::text
WHERE scope IN (
        'dashboard:report_analysis',
        'cron:official_daily_report',
        'cron:official_visual'
    )
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'threshold_unit_seeded_by' = 'migration_296'
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'threshold_unit' = 'ratio';

UPDATE vkpi_provider_budget_caps
SET metadata_json = (pg_temp.vkpi_296_try_parse_jsonb(metadata_json) - 'threshold_unit_seeded_by')::text
WHERE scope IN (
        'dashboard:report_analysis',
        'cron:official_daily_report',
        'cron:official_visual'
    )
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'threshold_unit_seeded_by' = 'migration_296';

UPDATE vkpi_provider_budget_caps
SET metadata_json = (pg_temp.vkpi_296_try_parse_jsonb(metadata_json) - 'window' - 'window_seeded_by')::text
WHERE scope IN (
        'dashboard:report_analysis',
        'cron:official_daily_report',
        'cron:official_visual'
    )
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window_seeded_by' = 'migration_296'
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window' = 'daily';

UPDATE vkpi_provider_budget_caps
SET metadata_json = (pg_temp.vkpi_296_try_parse_jsonb(metadata_json) - 'window_seeded_by')::text
WHERE scope IN (
        'dashboard:report_analysis',
        'cron:official_daily_report',
        'cron:official_visual'
    )
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window_seeded_by' = 'migration_296';

UPDATE vkpi_provider_budget_caps
SET metadata_json = (
    pg_temp.vkpi_296_try_parse_jsonb(metadata_json)
    - 'thresholds_repaired_by'
    - 'legacy_warning_at'
    - 'legacy_hard_stop_at'
)::text
WHERE scope IN (
        'dashboard:report_analysis',
        'cron:official_daily_report',
        'cron:official_visual'
    )
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'thresholds_repaired_by' = 'migration_296';

-- Restore migration-292's original description only when the 296 value remains
-- intact.  Otherwise preserve the operator's window and remove only our marker.
UPDATE vkpi_provider_budget_caps
SET metadata_json = (
    (
        pg_temp.vkpi_296_try_parse_jsonb(metadata_json)
        - 'window_repaired_by'
        - 'legacy_window'
    ) || '{"window":"manual_monthly"}'::jsonb
)::text
WHERE scope IN ('agent_skill', 'agent_alert_explain')
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window_repaired_by' = 'migration_296'
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'legacy_window' = 'manual_monthly'
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window' = 'monthly';

UPDATE vkpi_provider_budget_caps
SET metadata_json = (pg_temp.vkpi_296_try_parse_jsonb(metadata_json) - 'window_repaired_by' - 'legacy_window')::text
WHERE scope IN ('agent_skill', 'agent_alert_explain')
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window_repaired_by' = 'migration_296';

-- Undo the descriptive metric-tracking field only if it is still our value.
UPDATE vkpi_provider_budget_caps
SET metadata_json = (pg_temp.vkpi_296_try_parse_jsonb(metadata_json) - 'window' - 'window_seeded_by')::text
WHERE scope = 'metric_tracking'
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window_seeded_by' = 'migration_296'
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window' = 'monthly';

UPDATE vkpi_provider_budget_caps
SET metadata_json = (pg_temp.vkpi_296_try_parse_jsonb(metadata_json) - 'window_seeded_by')::text
WHERE scope = 'metric_tracking'
  AND pg_temp.vkpi_296_try_parse_jsonb(metadata_json) ->> 'window_seeded_by' = 'migration_296';

-- Exact-seed + current UTC boundary matching makes rollback conservative even
-- when an operator edited only metadata or reset_at (fields the old predicate
-- did not inspect).  A stale, rolled, or operator-touched row is retained.
DELETE FROM vkpi_provider_budget_caps AS budget
USING (
    VALUES
      ('kol_smart_search_query_plan', 10.00::numeric, 'fallback_to_rule_query_plan', 'monthly', '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"kol_smart_search_query_plan"}'::jsonb),
      ('vkpi_intelligent_ask', 10.00::numeric, 'evidence_only_response', 'monthly', '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"vkpi_intelligent_ask"}'::jsonb),
      ('vkpi_kol_outreach_draft', 10.00::numeric, 'skip_llm_keep_last', 'monthly', '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"vkpi_kol_outreach_draft"}'::jsonb),
      ('vkpi_kol_outreach_optimize', 10.00::numeric, 'skip_llm_keep_input', 'monthly', '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"vkpi_kol_outreach_optimize"}'::jsonb),
      ('vkpi_sentiment', 10.00::numeric, 'fallback_to_rule_v0', 'monthly', '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"vkpi_sentiment"}'::jsonb),
      ('vkpi_pillar', 10.00::numeric, 'fallback_to_rule_v0', 'monthly', '{"seeded_by":"migration_296","tier":"feature","window":"monthly","cost_tag":"vkpi_pillar"}'::jsonb),
      ('cron:vkpi_weekly_summary', 2.00::numeric, 'fallback_to_template', 'daily', '{"seeded_by":"migration_296","tier":"cron","window":"daily","cost_tag":"cron:vkpi_weekly_summary"}'::jsonb),
      ('cron:vkpi_bio_translate', 2.00::numeric, 'keep_original_text', 'daily', '{"seeded_by":"migration_296","tier":"cron","window":"daily","cost_tag":"cron:vkpi_bio_translate"}'::jsonb),
      ('cron:kol_outreach_pack', 2.00::numeric, 'skip_llm_keep_last', 'daily', '{"seeded_by":"migration_296","tier":"cron","window":"daily","cost_tag":"cron:kol_outreach_pack"}'::jsonb),
      ('cron:gemini_video_legacy', 2.00::numeric, 'fallback_to_cached_analysis', 'daily', '{"seeded_by":"migration_296","tier":"cron","window":"daily","cost_tag":"cron:gemini_video_legacy"}'::jsonb),
      ('cron:marketing_advisor', 2.00::numeric, 'fallback_to_evidence_only', 'daily', '{"seeded_by":"migration_296","tier":"advisor","window":"daily","cost_tag":"cron:marketing_advisor"}'::jsonb),
      ('agent_skill', 40.00::numeric, 'rule_mode_dry_run', 'monthly', '{"seeded_by":"migration_296","tier":"agent","window":"monthly","package":"skill_auto_orchestrate"}'::jsonb),
      ('metric_tracking', 30.00::numeric, 'pause_tracking_enqueue', 'monthly', '{"seeded_by":"migration_296","tier":"feature","window":"monthly","provider":"apify","cost_tag":"metric_tracking","cap_env":"VKPI_METRIC_TRACKING_MONTHLY_CAP_USD"}'::jsonb),
      ('agent_alert_explain', 5.00::numeric, 'rule_explanation', 'monthly', '{"seeded_by":"migration_296","tier":"agent","window":"monthly","package":"anomaly_sentinel_explain"}'::jsonb)
) AS seed(scope, cap_usd, fallback_action, reset_window, metadata_json)
WHERE budget.scope = seed.scope
  AND budget.cap_usd = seed.cap_usd
  AND budget.current_spend = 0
  AND budget.warning_at = 0.80
  AND budget.hard_stop_at = 1.00
  AND budget.fallback_action = seed.fallback_action
  AND pg_temp.vkpi_296_try_parse_jsonb(budget.metadata_json) = seed.metadata_json
  AND budget.reset_at = CASE seed.reset_window
        WHEN 'daily' THEN
          (date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 day') AT TIME ZONE 'UTC'
        ELSE
          (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 month') AT TIME ZONE 'UTC'
      END;

DROP FUNCTION pg_temp.vkpi_296_try_parse_jsonb(TEXT);

DELETE FROM schema_migrations
WHERE version_key = '296_vkpi_budget_scope_registry.sql';
