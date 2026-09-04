DROP INDEX IF EXISTS idx_apify_jobs_kol_search_inventory_source_created;
DROP TABLE IF EXISTS vkpi_kol_search_inventory_daily_slots;

-- Deliberately retain the disabled registry row and any operator-selected
-- enabled state. A down migration must not silently overwrite live controls;
-- reapplying the forward migration deliberately forces this row OFF again.

DELETE FROM schema_migrations
WHERE version_key='310_vkpi_kol_search_refresh_scheduler.sql';
