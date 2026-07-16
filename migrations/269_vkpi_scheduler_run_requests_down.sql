DROP INDEX IF EXISTS idx_scheduler_run_requests_dispatch;
DROP INDEX IF EXISTS uq_scheduler_run_requests_queued_task;
DROP TABLE IF EXISTS vkpi_scheduler_run_requests;

DELETE FROM schema_migrations
WHERE version_key = '269_vkpi_scheduler_run_requests.sql';
