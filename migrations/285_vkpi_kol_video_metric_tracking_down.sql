DELETE FROM scheduler_tasks
WHERE task_key = 'vkpi_kol_video_metric_refresh';

DROP TABLE IF EXISTS vkpi_kol_video_metric_tracking;

DELETE FROM schema_migrations
WHERE version_key = '285_vkpi_kol_video_metric_tracking.sql';
