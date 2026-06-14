-- 127 回滚:移除 Agent-OS data-freshness 附加列与索引。
-- 仅删除本迁移新增的 nullable 列,既有列(updated_at / last_sync_at / viltrox_fit_score)不受影响。
DROP INDEX IF EXISTS idx_vkpi_kol_pool_freshness;

ALTER TABLE vkpi_employee_channels
    DROP COLUMN IF EXISTS next_due,
    DROP COLUMN IF EXISTS last_checked;

ALTER TABLE vkpi_kol_pool
    DROP COLUMN IF EXISTS stale_reason,
    DROP COLUMN IF EXISTS next_due,
    DROP COLUMN IF EXISTS last_checked;
