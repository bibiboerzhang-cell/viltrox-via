DROP INDEX IF EXISTS idx_vkpi_llm_reservation_cost_scope_open;
DROP INDEX IF EXISTS idx_vkpi_llm_reservation_provider_open;
DROP INDEX IF EXISTS idx_vkpi_llm_reservation_open;
DROP TABLE IF EXISTS vkpi_llm_budget_reservations;

DELETE FROM schema_migrations
WHERE version_key = '258_vkpi_llm_budget_reservations.sql';
