-- 273: allow the 'expired' reservation state (stale-reservation reaper).
--
-- Why: 'unknown' deliberately keeps consuming allowance (fail-closed for
-- unverifiable provider outcomes) but nothing ever reclaimed it -- 81 stale
-- rows were permanently holding $5.84 of estimated allowance and had already
-- eaten 16% of the competitor-radar daily cap. The reaper moves open states
-- older than a TTL to 'expired', which _open_reserved_for_scope never counts.
--
-- The migration runner owns the transaction; no BEGIN/COMMIT here.

ALTER TABLE vkpi_llm_budget_reservations
  DROP CONSTRAINT IF EXISTS ck_vkpi_llm_reservation_state;

ALTER TABLE vkpi_llm_budget_reservations
  ADD CONSTRAINT ck_vkpi_llm_reservation_state CHECK (
    state IN ('reserved', 'provider_started', 'unknown', 'settled', 'released', 'blocked', 'expired')
  );
