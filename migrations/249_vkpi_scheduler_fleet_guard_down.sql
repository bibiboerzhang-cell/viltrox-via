BEGIN;
DROP TABLE IF EXISTS vkpi_scheduler_fire_claims;
DELETE FROM schema_migrations
WHERE version_key = '249_vkpi_scheduler_fleet_guard.sql';
COMMIT;
