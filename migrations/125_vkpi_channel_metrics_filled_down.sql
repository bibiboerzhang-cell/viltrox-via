-- 125 回滚:删除官方账号每日指标补缺表及其索引。
-- 本表为隔离派生表,源表 vkpi_channel_metrics 不受影响。
DROP INDEX IF EXISTS idx_vkpi_channel_metrics_filled_date;
DROP TABLE IF EXISTS vkpi_channel_metrics_filled;
