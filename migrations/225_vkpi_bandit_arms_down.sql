-- 225 回滚:删 bandit-lite arm 权重账表 + 放权刷新闸门种子行。
-- 注意:arm 统计(n/mean_reward/last_reward)随表删除,回滚前如需留档请先自行导出;
-- market_brain/bandit 在表缺失时诚实降级(record/load 回明确 reason 或空,绝不炸)。
BEGIN;
DELETE FROM scheduler_tasks WHERE task_key = 'vkpi_bandit_weight_refresh';
DROP TABLE IF EXISTS vkpi_bandit_arms;
COMMIT;
