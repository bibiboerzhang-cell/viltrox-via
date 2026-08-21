DROP TABLE IF EXISTS vkpi_content_metric_snapshots;

-- Forward migrations are registered by app.db.connection after execution.
-- A manually invoked rollback must remove that durable apply marker so a
-- subsequent startup can apply the forward migration again.
DELETE FROM schema_migrations
WHERE version_key = '283_vkpi_content_metric_snapshots.sql';
