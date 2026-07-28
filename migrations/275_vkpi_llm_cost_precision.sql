-- 275: keep LLM cost mirrors and cumulative caps at micro-USD precision.
--
-- vkpi_llm_calls.cost_micro_usd and vkpi_llm_budget_reservations already store
-- six-decimal USD exactly.  The older mirror/cap columns rounded the same call
-- to four or two decimals, so a valid $0.000033 canary became $0.0000 and a
-- sub-cent cap could become zero (which means unlimited to the budget guard).
--
-- The migration runner owns the surrounding transaction; no BEGIN/COMMIT here.

ALTER TABLE vkpi_ai_cost_ledger
  ALTER COLUMN cost_usd TYPE NUMERIC(18, 6)
  USING cost_usd::NUMERIC(18, 6);

ALTER TABLE vkpi_provider_budget_caps
  ALTER COLUMN cap_usd TYPE NUMERIC(18, 6)
  USING cap_usd::NUMERIC(18, 6),
  ALTER COLUMN current_spend TYPE NUMERIC(18, 6)
  USING current_spend::NUMERIC(18, 6);
