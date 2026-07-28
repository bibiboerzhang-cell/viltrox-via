-- 275 down: narrowing is allowed only when it cannot discard any stored value.
-- A release that has recorded micro-USD values must roll back to its database
-- clone instead of silently rounding live accounting data.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM vkpi_ai_cost_ledger
    WHERE cost_usd IS NOT NULL
      AND (
        cost_usd <> ROUND(cost_usd, 4)
        OR ABS(cost_usd) >= 1000000
      )
  ) THEN
    RAISE EXCEPTION '275 down refused: vkpi_ai_cost_ledger.cost_usd would lose precision';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM vkpi_provider_budget_caps
    WHERE cap_usd IS NOT NULL
      AND (
        cap_usd <> ROUND(cap_usd, 2)
        OR ABS(cap_usd) >= 100000000
      )
  ) THEN
    RAISE EXCEPTION '275 down refused: vkpi_provider_budget_caps.cap_usd would lose precision';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM vkpi_provider_budget_caps
    WHERE current_spend IS NOT NULL
      AND (
        current_spend <> ROUND(current_spend, 4)
        OR ABS(current_spend) >= 1000000
      )
  ) THEN
    RAISE EXCEPTION '275 down refused: vkpi_provider_budget_caps.current_spend would lose precision';
  END IF;
END;
$$;

ALTER TABLE vkpi_ai_cost_ledger
  ALTER COLUMN cost_usd TYPE NUMERIC(10, 4)
  USING cost_usd::NUMERIC(10, 4);

ALTER TABLE vkpi_provider_budget_caps
  ALTER COLUMN cap_usd TYPE NUMERIC(10, 2)
  USING cap_usd::NUMERIC(10, 2),
  ALTER COLUMN current_spend TYPE NUMERIC(10, 4)
  USING current_spend::NUMERIC(10, 4);

DELETE FROM schema_migrations
WHERE version_key = '275_vkpi_llm_cost_precision.sql';
