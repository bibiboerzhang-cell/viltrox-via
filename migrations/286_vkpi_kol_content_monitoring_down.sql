DELETE FROM scheduler_tasks
WHERE task_key='vkpi_kol_content_monitoring';

-- Deliberately do not re-enable the retired implicit kol_auto_poll task.
DROP TABLE IF EXISTS vkpi_kol_content_monitoring_subscriptions;

DELETE FROM schema_migrations
WHERE version_key='286_vkpi_kol_content_monitoring.sql';
