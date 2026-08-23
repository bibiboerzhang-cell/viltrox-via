-- 回滚 290:搜索页反馈写口列 + 三条 cron 闸门种子。
-- 注意:无推荐行的搜索反馈(recommendation_id IS NULL)会随 NOT NULL 恢复而被删除,回滚前如需留档请先导出。

DELETE FROM scheduler_tasks
WHERE task_key IN ('vkpi_forecast_batch_issue', 'vkpi_weekly_offline_eval', 'vkpi_anomaly_sentinel');

DROP INDEX IF EXISTS idx_vkpi_reco_feedback_pool;
DROP INDEX IF EXISTS uq_vkpi_reco_feedback_search_dedupe;

DELETE FROM vkpi_recommendation_feedback WHERE recommendation_id IS NULL;

ALTER TABLE vkpi_recommendation_feedback DROP COLUMN IF EXISTS reason;
ALTER TABLE vkpi_recommendation_feedback DROP COLUMN IF EXISTS kol_pool_id;
ALTER TABLE vkpi_recommendation_feedback DROP COLUMN IF EXISTS source;

ALTER TABLE vkpi_recommendation_feedback
    ALTER COLUMN recommendation_id SET NOT NULL;

DELETE FROM schema_migrations
WHERE version_key='290_vkpi_recommendation_feedback_search.sql';
