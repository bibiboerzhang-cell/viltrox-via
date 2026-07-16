DROP INDEX IF EXISTS idx_vkpi_llm_fleet_breaker_half_open_lease;
DROP INDEX IF EXISTS idx_vkpi_llm_fleet_breaker_opened_until;
DROP TABLE IF EXISTS vkpi_llm_fleet_breakers;

DELETE FROM schema_migrations
WHERE version_key = '266_vkpi_llm_fleet_breaker.sql';
