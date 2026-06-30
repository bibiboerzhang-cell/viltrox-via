-- Revert 203: drop the micro-USD precision cost column on the LLM ledger.
-- Pure additive column, so the down path simply removes it. cost_cents (the
-- legacy integer column) is untouched and remains the pre-203 source of spend.
BEGIN;
ALTER TABLE vkpi_llm_calls DROP COLUMN IF EXISTS cost_micro_usd;
COMMIT;
