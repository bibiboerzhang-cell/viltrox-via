-- Roll back only an untouched migration-297 default.  Rows inserted by 296,
-- edited by an operator, carrying spend, or already rolled to a new window are
-- retained.

CREATE OR REPLACE FUNCTION pg_temp.vkpi_297_try_parse_jsonb(raw_text TEXT)
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

DELETE FROM vkpi_provider_budget_caps
WHERE scope = 'cron:marketing_advisor'
  AND cap_usd = 2.00
  AND current_spend = 0
  AND warning_at = 0.80
  AND hard_stop_at = 1.00
  AND fallback_action = 'fallback_to_evidence_only'
  AND pg_temp.vkpi_297_try_parse_jsonb(metadata_json) = '{"seeded_by":"migration_297","tier":"advisor","window":"daily","cost_tag":"cron:marketing_advisor"}'::jsonb
  AND reset_at = (
      date_trunc('day', CURRENT_TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL '1 day'
  ) AT TIME ZONE 'UTC';

DROP FUNCTION pg_temp.vkpi_297_try_parse_jsonb(TEXT);

DELETE FROM schema_migrations
WHERE version_key = '297_vkpi_marketing_advisor_budget.sql';
