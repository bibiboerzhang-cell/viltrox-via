-- 回滚 288:推荐特征快照与影子重排序模型账本(纯派生,可由引擎重放重建)。

DROP TABLE IF EXISTS vkpi_recommendation_rerank_model;
DROP TABLE IF EXISTS vkpi_recommendation_feature_snapshot;

DELETE FROM schema_migrations
WHERE version_key='288_vkpi_recommendation_feature_snapshot.sql';
