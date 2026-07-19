-- 273 down: restore the original state whitelist (expired rows must be
-- re-labelled first or this constraint re-add will fail).
UPDATE vkpi_llm_budget_reservations SET state='released' WHERE state='expired';
ALTER TABLE vkpi_llm_budget_reservations
  DROP CONSTRAINT IF EXISTS ck_vkpi_llm_reservation_state;
ALTER TABLE vkpi_llm_budget_reservations
  ADD CONSTRAINT ck_vkpi_llm_reservation_state CHECK (
    state IN ('reserved', 'provider_started', 'unknown', 'settled', 'released', 'blocked')
  );
