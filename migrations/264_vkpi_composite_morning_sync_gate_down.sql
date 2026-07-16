DELETE FROM scheduler_tasks
WHERE task_key = 'vkpi_morning_sync';

DELETE FROM schema_migrations
WHERE version_key = '264_vkpi_composite_morning_sync_gate.sql';
