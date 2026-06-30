-- 202_vkpi_market_observations_down.sql — 回滚:丢弃市场观察快照表及其索引。
-- 仅在确需回退时使用;additive 表,删除不影响实时合成路径(实时仍内存返回)。
BEGIN;
DROP INDEX IF EXISTS idx_market_observations_kind;
DROP INDEX IF EXISTS idx_market_observations_generated_at;
DROP INDEX IF EXISTS uq_market_observations_topic_kind_date;
DROP TABLE IF EXISTS vkpi_market_observations;
COMMIT;
