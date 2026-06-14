-- 127: Agent-OS data-freshness observability — additive, nullable per-entity stamping.
-- 红线:仅 ADD COLUMN IF NOT EXISTS(可重跑幂等);零触 viltrox_fit_score / rule_v0 / 既有列。
-- 这些列起始全 NULL,供未来「显式打戳」用;读层(data_freshness.py)当前 COALESCE 回退到
-- 既有时间戳(vkpi_kol_pool.updated_at / vkpi_employee_channels.last_sync_at),无须等本迁移写值即可工作。
-- 回滚见 127_vkpi_data_freshness_down.sql。

ALTER TABLE vkpi_kol_pool
    ADD COLUMN IF NOT EXISTS last_checked TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS next_due TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS stale_reason TEXT NULL;

ALTER TABLE vkpi_employee_channels
    ADD COLUMN IF NOT EXISTS last_checked TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS next_due TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_freshness
    ON vkpi_kol_pool(next_due);

COMMENT ON COLUMN vkpi_kol_pool.last_checked IS
    'Agent-OS data-freshness:最近一次显式新鲜度核查时刻(NULL=尚未显式打戳,读层回退 updated_at)。';
COMMENT ON COLUMN vkpi_kol_pool.next_due IS
    'Agent-OS data-freshness:下次应核查时刻(NULL=未排期)。';
COMMENT ON COLUMN vkpi_kol_pool.stale_reason IS
    'Agent-OS data-freshness:陈旧原因标记(NULL=无)。';
COMMENT ON COLUMN vkpi_employee_channels.last_checked IS
    'Agent-OS data-freshness:最近一次显式新鲜度核查时刻(NULL=回退 last_sync_at)。';
COMMENT ON COLUMN vkpi_employee_channels.next_due IS
    'Agent-OS data-freshness:下次应核查时刻(NULL=未排期)。';
